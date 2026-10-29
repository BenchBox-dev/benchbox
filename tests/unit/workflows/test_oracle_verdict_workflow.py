from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter
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
STEP_REF = re.compile(r"steps\.([\w-]+)\.(?:outputs\.([\w-]+)|(outcome))")
DRIVE_PHASE = re.compile(r"\bcli drive(?: --policy \S+)? (\w+)")
SETUP_ALLOWANCE_MINUTES = 30
JOB_TIMEOUT_CEILING_MINUTES = 360
MAX_NON_LOOP_PHASE_MINUTES = 30
SWEEP_TIMEOUT_MINUTES = 8
UNCHANGED_PREFIX_SHA256 = "9b385521edbecce55a3c3f8dfeb4df5504498ff9c5409f253344f2b4348d74f6"
POLICY_FLAG = "--policy .github/oracle-reviewers.yml"
UV_RUN = "uv run --isolated --no-project --python 3.12 --with pyyaml==6.0.3 -- python -m"
PYTHON_RUN = "python3 -m"
CLI = "_project.scripts.oracle_reviewers.cli"
REVIEW_CONDITION = (
    "github.event_name == 'workflow_dispatch' ||"
    " (github.event_name == 'pull_request_target' && (github.event.action != 'edited' || github.event.changes.base)) ||"
    " (github.event_name == 'issue_comment' && github.event.issue.pull_request"
    " && startsWith(github.event.comment.body, '/oracle-review')"
    ' && contains(fromJSON(\'["OWNER", "MEMBER", "COLLABORATOR"]\'), github.event.comment.author_association))'
)
REFUSE_STEP = "Refuse a dispatch from any ref other than develop"
TRUSTED_STEP = "Check out the trusted develop copy"
PREPARE_STEP = "Prepare the review"
HEAD_STEP = "Check out the pull request head for reading only"
HARNESS_STEP = "Install omp"
JOIN_STEP = "Join the tailnet to reach the credential gateway"
JOIN_SCAN_STEP = "Scan the disk for secret values after the tailnet join"
ISOLATION_STEP = "Verify Linux process isolation"
LOOP_STEP = "Run the attempt loop"
FINALIZE_STEP = "Decide the verdict and redact every secret value from the results"
MINT_STEP = "Mint the App token"
PUBLISH_STEP = "Post the result"
RECORD_STEP = "Record the retry attempt"
STATE_STEP = "Upload the retry state"
CAPTURE_STEP = "Upload the captured reviewer event streams"
DISPATCH_STEP = "Dispatch the gate for the reviewed head"
SCAN_STEPS = (
    "Scan the disk for secret values before the reviewer runs",
    JOIN_SCAN_STEP,
    "Scan the disk for secret values after the reviewer exits",
)
RECOVER_STEPS = tuple(f"Classify the invalid attempt after attempt {after}" for after in range(1, POLICY.max_attempts))
LOOP_STEPS = (
    LOOP_STEP,
    *(f"Resume the attempt loop after attempt {after}" for after in range(1, POLICY.max_attempts)),
)
STEP_ORDER = [
    REFUSE_STEP,
    TRUSTED_STEP,
    "Install uv",
    PREPARE_STEP,
    HEAD_STEP,
    SCAN_STEPS[0],
    HARNESS_STEP,
    JOIN_STEP,
    JOIN_SCAN_STEP,
    ISOLATION_STEP,
    LOOP_STEP,
    *[
        name
        for after in range(1, POLICY.max_attempts)
        for name in (
            f"Classify the invalid attempt after attempt {after}",
            f"Resume the attempt loop after attempt {after}",
        )
    ],
    SCAN_STEPS[2],
    FINALIZE_STEP,
    MINT_STEP,
    PUBLISH_STEP,
    RECORD_STEP,
    STATE_STEP,
    *[f"Upload attempt {slot}" for slot in range(1, POLICY.max_attempts + 1)],
    CAPTURE_STEP,
    DISPATCH_STEP,
]
REDACTION_ENV = {
    "OMP_GATEWAY_TOKEN": "${{ secrets.ORACLE_GATEWAY_TOKEN }}",
    "TYPESAFE_API_KEY": "${{ secrets.TYPESAFE_API_KEY }}",
    "ORACLE_GATEWAY_URL": "${{ secrets.ORACLE_GATEWAY_URL }}",
    "TS_OAUTH_CI_CLIENT_ID": "${{ secrets.TS_OAUTH_CI_CLIENT_ID }}",
    "TS_OAUTH_CI_SECRET": "${{ secrets.TS_OAUTH_CI_SECRET }}",
    "JOB_TOKEN": "${{ github.token }}",
}
SCAN_ENV = {
    "TYPESAFE_API_KEY": "${{ secrets.TYPESAFE_API_KEY }}",
    "TS_OAUTH_CI_SECRET": "${{ secrets.TS_OAUTH_CI_SECRET }}",
    "JOB_TOKEN": "${{ github.token }}",
}
STEP_IDS = {
    "prepare": {"decision", "post", "pr", "head_sha"},
    "harness": {"state"},
    "finalize": {"state", "comment", "review", "has_state", "attempts"},
    "app": {"token"},
    "publish": {"skip", "state_recorded"},
}
EXPECTED_DRIVE_PHASES = {
    "prepare": 1,
    "harness": 1,
    "scan": 3,
    "isolation": 1,
    "attempts": POLICY.max_attempts,
    "recover": POLICY.max_attempts - 1,
    "finalize": 1,
    "publish": 1,
    "dispatch": 1,
}
ENVIRONMENT_FORCED_ACTIONS = (
    "actions/checkout@",
    "astral-sh/setup-uv@",
    "tailscale/github-action@",
    "actions/create-github-app-token@",
    "actions/upload-artifact@",
)


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


def _phase_steps() -> list[dict[str, Any]]:
    return [step for step in _steps() if ".cli drive " in step.get("run", "")]


def _longest_timeout() -> int:
    return max(reviewer.timeout_minutes for reviewer in POLICY.reviewers.values())


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
    budget = POLICY.max_attempts * _longest_timeout() + SETUP_ALLOWANCE_MINUTES
    assert _review()["timeout-minutes"] == budget
    assert budget < JOB_TIMEOUT_CEILING_MINUTES
    assert _review()["timeout-minutes"] > POLICY.max_attempts * _longest_timeout()


def test_every_phase_step_has_an_integer_timeout_below_the_job_timeout() -> None:
    phases = _phase_steps()
    assert phases
    loop_minutes = POLICY.max_attempts * _longest_timeout()
    for step in phases:
        timeout = step["timeout-minutes"]
        assert type(timeout) is int, step["name"]
        assert timeout < _review()["timeout-minutes"], step["name"]
        if step["run"].rstrip().endswith("drive attempts"):
            assert timeout == loop_minutes, step["name"]
        else:
            assert 0 < timeout <= MAX_NON_LOOP_PHASE_MINUTES, step["name"]


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


def test_the_review_job_env_holds_only_the_two_presence_flags() -> None:
    assert set(_review()["env"]) == {"HAS_TAILNET", "HAS_APP"}


def test_the_disk_scans_bracket_the_reviewer_and_hold_exactly_the_scanned_secrets() -> None:
    names = _names()
    before, joined, after = (_step(name) for name in SCAN_STEPS)
    assert names.index(PREPARE_STEP) < names.index(before["name"]) < names.index(HARNESS_STEP)
    assert names.index(JOIN_STEP) < names.index(joined["name"]) < names.index(ISOLATION_STEP) < names.index(LOOP_STEP)
    assert names.index(after["name"]) == names.index(LOOP_STEPS[-1]) + 1
    for step in (before, joined, after):
        assert step["env"] == SCAN_ENV
        assert step["if"] == "steps.prepare.outputs.decision == 'review'"
        assert step["run"] == f"{PYTHON_RUN} {CLI} drive scan"
        assert "continue-on-error" not in step


def test_the_isolation_step_runs_immediately_before_the_loop_and_fails_closed() -> None:
    step = _step(ISOLATION_STEP)
    assert _names()[_names().index(LOOP_STEP) - 1] == ISOLATION_STEP
    assert step["if"] == "steps.prepare.outputs.decision == 'review'"
    assert step["working-directory"] == "trusted"
    assert step["run"] == f"{PYTHON_RUN} {CLI} drive isolation"
    assert "continue-on-error" not in step
    assert "env" not in step


def test_the_loop_holds_only_the_gateway_token_and_url() -> None:
    for name in LOOP_STEPS:
        step = _step(name)
        assert step["env"] == {
            "OMP_GATEWAY_TOKEN": "${{ secrets.ORACLE_GATEWAY_TOKEN }}",
            "ORACLE_GATEWAY_URL": "${{ secrets.ORACLE_GATEWAY_URL }}",
        }, name
        assert step["working-directory"] == "trusted"
        assert step["run"] == f"{PYTHON_RUN} {CLI} drive attempts"
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
    trusted = _step(TRUSTED_STEP)
    assert ".github/oracle-omp-config.yml" in trusted["with"]["sparse-checkout"].split()
    assert trusted["with"]["path"] == "trusted"
    assert trusted["with"]["ref"] == "${{ github.sha }}"


def test_the_job_relies_on_the_per_pull_request_group_alone() -> None:
    assert "concurrency" not in _review()
    assert "strategy" not in _review()


def test_concurrency_is_per_pull_request_and_ignores_unrelated_events() -> None:
    group = _load()["concurrency"]["group"]
    assert "oracle-verdict-" in group
    assert "github.event.changes.base" in group


def test_dispatch_is_refused_outside_develop() -> None:
    guard = _step(REFUSE_STEP)
    assert guard["if"] == "github.event_name == 'workflow_dispatch'"
    assert guard["env"]["CURRENT_REF"] == "${{ github.ref }}"
    assert "exit 2" in guard["run"]
    assert _names()[0] == guard["name"]


def test_the_prepare_step_holds_the_job_token_the_pull_request_and_the_judgment_key() -> None:
    step = _step(PREPARE_STEP)
    assert step["id"] == "prepare"
    assert step["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "PR_NUMBER": "${{ github.event.pull_request.number || github.event.issue.number || inputs.pr }}",
        "TYPESAFE_API_KEY": "${{ secrets.TYPESAFE_API_KEY }}",
    }
    assert " ".join(step["run"].split()) == f"{UV_RUN} {CLI} drive {POLICY_FLAG} prepare"
    assert step["working-directory"] == "trusted"
    assert "if" not in step


def test_every_bucket_reviewer_resolves() -> None:
    assert set(POLICY.reviewers) == set(SIZE_BUCKETS)
    assert set(POLICY.buckets) == set(SIZE_BUCKETS)
    for bucket in SIZE_BUCKETS:
        assert POLICY.reviewers[bucket].harness == "omp"


def test_only_the_prepare_the_scans_the_recovery_and_the_finalize_steps_hold_the_judgment_key() -> None:
    assert set(_steps_referencing("TYPESAFE_API_KEY")) == {PREPARE_STEP, *SCAN_STEPS, *RECOVER_STEPS, FINALIZE_STEP}
    for name in _steps_referencing("TYPESAFE_API_KEY"):
        assert name not in LOOP_STEPS


def test_every_recovery_step_runs_between_two_loop_steps_and_holds_only_the_judgment_key() -> None:
    names = _names()
    assert len(RECOVER_STEPS) == POLICY.max_attempts - 1
    for after, name in enumerate(RECOVER_STEPS, start=1):
        step = _step(name)
        assert step["env"] == {"TYPESAFE_API_KEY": "${{ secrets.TYPESAFE_API_KEY }}"}
        assert step["run"] == f"{PYTHON_RUN} {CLI} drive recover"
        assert "continue-on-error" not in step
        assert "always()" not in step["if"]
        assert names.index(LOOP_STEPS[after - 1]) + 1 == names.index(name)
        assert names.index(name) + 1 == names.index(LOOP_STEPS[after])
        assert step["if"] == _step(LOOP_STEPS[after])["if"]


def test_the_only_step_referencing_the_app_key_mints_after_the_loop_and_the_finalize_step() -> None:
    assert _steps_referencing("ORACLE_APP_PRIVATE_KEY") == [MINT_STEP]
    names = _names()
    mint = names.index(MINT_STEP)
    assert mint == names.index(FINALIZE_STEP) + 1
    assert mint > names.index(LOOP_STEPS[-1])
    assert mint > names.index(SCAN_STEPS[2])
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
    assert " ".join(dispatch["run"].split()) == f"{UV_RUN} {CLI} drive {POLICY_FLAG} sweep"
    assert dispatch["timeout-minutes"] == SWEEP_TIMEOUT_MINUTES
    assert _jobs()["sweep"]["timeout-minutes"] > SWEEP_TIMEOUT_MINUTES


def test_every_secret_is_referenced_only_where_it_is_needed() -> None:
    allowed = {
        "ORACLE_GATEWAY_TOKEN": {*LOOP_STEPS, FINALIZE_STEP},
        "ORACLE_GATEWAY_URL": {*LOOP_STEPS, FINALIZE_STEP},
        "TS_OAUTH_CI_CLIENT_ID": {JOIN_STEP, FINALIZE_STEP},
        "TS_OAUTH_CI_SECRET": {JOIN_STEP, *SCAN_STEPS, FINALIZE_STEP},
        "ORACLE_APP_ID": {MINT_STEP},
    }
    for secret, steps in allowed.items():
        assert set(_steps_referencing(secret)) == steps, secret
    declared = set().union(*(_secrets(step) for step in _steps()))
    assert declared == {*allowed, "TYPESAFE_API_KEY", "ORACLE_APP_PRIVATE_KEY"}


def test_the_finalize_step_holds_every_secret_value_the_job_held() -> None:
    step = _step(FINALIZE_STEP)
    assert step["id"] == "finalize"
    assert step["env"] == {**REDACTION_ENV, "OMP_INSTALL_STATE": "${{ steps.harness.outputs.state }}"}
    assert step["run"] == f"{PYTHON_RUN} {CLI} drive finalize"
    assert step["if"] == "steps.prepare.outputs.post == 'true'"
    assert "continue-on-error" not in step


def test_the_publish_step_holds_only_the_app_token_and_the_job_token() -> None:
    step = _step(PUBLISH_STEP)
    assert step["id"] == "publish"
    assert step["env"] == {
        "GH_TOKEN": "${{ steps.app.outputs.token }}",
        "JOB_TOKEN": "${{ github.token }}",
    }
    assert step["run"] == f"{PYTHON_RUN} {CLI} drive publish"
    assert step["if"] == "steps.prepare.outputs.post == 'true'"
    assert _names().index(PUBLISH_STEP) == _names().index(MINT_STEP) + 1
    assert "continue-on-error" not in step


def test_the_mint_step_runs_only_when_a_result_is_to_be_posted_and_the_app_is_configured() -> None:
    mint = _step(MINT_STEP)
    assert mint["id"] == "app"
    assert mint["uses"] == "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
    assert mint["if"] == "steps.prepare.outputs.post == 'true' && env.HAS_APP == 'true'"
    assert mint["with"]["permission-statuses"] == "write"
    assert mint["with"]["permission-pull-requests"] == "write"
    assert _review()["env"]["HAS_APP"] == "${{ secrets.ORACLE_APP_ID != '' }}"
    assert not any(str(step.get("uses", "")).startswith("actions/download-artifact@") for step in _steps())


def test_no_step_summary_write_reaches_the_summary_before_the_finalize_step() -> None:
    names = _names()
    writers = [step["name"] for step in _steps()[: names.index(FINALIZE_STEP)] if "GITHUB_STEP_SUMMARY" in _text(step)]
    assert writers == []


def test_the_job_token_is_never_present_while_the_reviewer_runs() -> None:
    holders = [step["name"] for step in _steps() if "github.token" in _text(step)]
    assert set(holders) == {PREPARE_STEP, *SCAN_STEPS, FINALIZE_STEP, PUBLISH_STEP, DISPATCH_STEP}
    for name in holders:
        step = _step(name)
        assert name not in LOOP_STEPS and name not in RECOVER_STEPS
        assert "github.token" in _text(step.get("env", {})), name
        assert "github.token" not in _text({key: value for key, value in step.items() if key != "env"}), name
    assert "github.token" not in _text(_review()["env"])
    assert _load()["permissions"] == {}


def test_the_pull_request_head_is_data_and_never_executed() -> None:
    checkouts = [step for step in _steps() if str(step.get("uses", "")).startswith("actions/checkout@")]
    trusted, head = checkouts
    assert trusted["with"]["ref"] == "${{ github.sha }}"
    assert trusted["with"]["path"] == "trusted"
    assert head["with"]["ref"] == "${{ steps.prepare.outputs.head_sha }}"
    assert head["with"]["path"] == "workspace"
    assert head["if"] == "steps.prepare.outputs.decision == 'review'"
    for step in _steps():
        if "run" in step:
            assert step.get("working-directory") in (None, "trusted"), step["name"]
            assert "bash -c" not in step["run"]
            assert "workspace/" not in step["run"].replace("$GITHUB_WORKSPACE/workspace", "")


def test_every_checkout_disables_persisted_credentials() -> None:
    checkouts = [step for step in _steps() if str(step.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 2
    for step in checkouts:
        assert step["with"]["persist-credentials"] is False
    sweep = [step for step in _jobs()["sweep"]["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")]
    assert all(step["with"]["persist-credentials"] is False for step in sweep)


def test_omp_is_installed_by_the_harness_phase_under_uv_with_yaml() -> None:
    text = _text_of_workflow()
    for gone in (
        "npm install",
        "setup-node",
        "dev.meta.ai",
        "antigravity",
        "codex login",
        "sysctl",
        "CODEX_HOME",
        "AGY_CLI_DISABLE_AUTO_UPDATE",
        "curl ",
        "OMP_URL",
        "OMP_SHA256",
        "OMP_VERSION",
        "--dangerously",
        "--yolo",
        "install-omp",
    ):
        assert gone not in text, gone
    install = _step(HARNESS_STEP)
    assert install["id"] == "harness"
    assert "continue-on-error" not in install
    assert "env" not in install
    assert install["working-directory"] == "trusted"
    assert install["if"] == "steps.prepare.outputs.decision == 'review'"
    assert "--with pyyaml==6.0.3" in install["run"]
    assert " ".join(install["run"].split()) == f"{UV_RUN} {CLI} drive {POLICY_FLAG} harness"
    assert _text(_step(LOOP_STEP)).count("steps.harness.outputs.state == 'installed'") == 1
    for name in LOOP_STEPS:
        assert "steps.harness.outputs.state == 'installed'" in _step(name)["if"]
    for name in RECOVER_STEPS:
        assert "steps.harness.outputs.state == 'installed'" in _step(name)["if"]
    assert _step(FINALIZE_STEP)["env"]["OMP_INSTALL_STATE"] == "${{ steps.harness.outputs.state }}"


def test_the_pin_file_keeps_the_linux_build_the_workflow_used_to_install() -> None:
    raw = yaml.safe_load((REPO_ROOT / ".github/oracle-reviewers.yml").read_text(encoding="utf-8"))["omp"]
    linux = raw["builds"]["linux-x64"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", raw["version"])
    assert linux["url"].startswith("https://build.stencil.so/d/omp/") and linux["url"].endswith("/omp-linux-x64")
    pin = pin_from_text((REPO_ROOT / ".github" / "oracle-reviewers.yml").read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin.version)
    assert pin.builds["linux-x64"].url == linux["url"]
    assert re.fullmatch(r"[0-9a-f]{64}", pin.builds["linux-x64"].sha256)


def test_setup_failures_become_absences_and_every_other_step_fails_the_job_closed() -> None:
    tolerated = {HEAD_STEP, JOIN_STEP}
    assert {step["name"] for step in _steps() if "continue-on-error" in step} == tolerated
    for name in tolerated:
        assert _step(name)["continue-on-error"] is True
    assert "always()" not in _step(LOOP_STEP)["if"]
    assert "cancelled()" not in _step(LOOP_STEP)["if"]
    for step in _phase_steps():
        assert "always()" not in step.get("if", ""), step["name"]
        assert "cancelled()" not in step.get("if", ""), step["name"]


def test_step_outputs_are_wired_to_earlier_steps_and_keys_the_driver_writes() -> None:
    steps = _steps()
    ids = [step["id"] for step in steps if "id" in step]
    assert sorted(ids) == sorted(STEP_IDS)
    position = {step["id"]: index for index, step in enumerate(steps) if "id" in step}
    references = 0
    for index, step in enumerate(steps):
        for ident, output, outcome in STEP_REF.findall(_text(step)):
            references += 1
            assert ident in STEP_IDS, (step["name"], ident)
            assert position[ident] < index, (step["name"], ident)
            if outcome:
                assert ident in {"finalize", "publish"}, (step["name"], ident)
            else:
                assert output in STEP_IDS[ident], (step["name"], ident, output)
    assert references
    text = _text_of_workflow()
    for removed in (
        "plan",
        "classify",
        "omp",
        "count",
        "guard",
        "status",
        "comment",
        "review",
        "loop",
        "redact",
        "dispatch",
        "shadow",
    ):
        assert not re.search(rf"steps\.{removed}[\w-]*\.", text), removed


def test_the_review_job_runs_exactly_one_driver_step_per_phase_invocation() -> None:
    phases = Counter(
        match.group(1) for step in _steps() for match in [DRIVE_PHASE.search(step.get("run", ""))] if match
    )
    assert phases == EXPECTED_DRIVE_PHASES
    assert all(len(DRIVE_PHASE.findall(step.get("run", ""))) <= 1 for step in _steps())
    sweeps = [step for step in _jobs()["sweep"]["steps"] if DRIVE_PHASE.search(step.get("run", ""))]
    assert [DRIVE_PHASE.search(step["run"]).group(1) for step in sweeps] == ["sweep"]
    assert "cli sweep" not in _text_of_workflow()


def test_the_only_steps_not_running_a_driver_phase_are_environment_forced() -> None:
    phase_names = {step["name"] for step in _phase_steps()}
    others = [step for step in _steps() if step["name"] not in phase_names]
    assert phase_names and others
    for step in others:
        if "uses" in step:
            assert step["uses"].startswith(ENVIRONMENT_FORCED_ACTIONS), step["name"]
        else:
            assert step["name"] == REFUSE_STEP
    assert {step["name"] for step in others if "uses" not in step} == {REFUSE_STEP}
    for step in _phase_steps():
        assert "uses" not in step
        assert step["run"].count("\n") <= 1


def test_the_gate_dispatch_is_the_last_step_and_needs_a_posted_result() -> None:
    names = _names()
    assert names[-1] == DISPATCH_STEP
    step = _step(DISPATCH_STEP)
    assert step["if"] == (
        "steps.prepare.outputs.post == 'true' && env.HAS_APP == 'true' && steps.publish.outputs.skip != 'true'"
    )
    assert step["env"] == {
        "GH_TOKEN": "${{ steps.app.outputs.token }}",
        "JOB_TOKEN": "${{ github.token }}",
    }
    assert step["run"] == f"{PYTHON_RUN} {CLI} drive dispatch"
    assert step["timeout-minutes"] <= MAX_NON_LOOP_PHASE_MINUTES
    assert names.index(DISPATCH_STEP) > names.index(PUBLISH_STEP)
    for name in (RECORD_STEP, STATE_STEP):
        assert names.index(DISPATCH_STEP) > names.index(name)


def test_only_the_review_job_dispatches_the_gate_and_only_through_the_driver() -> None:
    text = _text_of_workflow()
    assert "gh workflow run" not in text
    assert "oracle-gate.yml" not in text
    dispatchers = [
        (name, step["name"])
        for name, job in _jobs().items()
        for step in job["steps"]
        if re.search(r"\bdrive dispatch\b", step.get("run", ""))
    ]
    assert dispatchers == [("review", DISPATCH_STEP)]


def test_the_retry_attempt_is_recorded_after_a_successful_finalize_whatever_the_post_does() -> None:
    names = _names()
    record = _step(RECORD_STEP)
    assert names.index(RECORD_STEP) > names.index(PUBLISH_STEP)
    assert (
        record["if"] == "always() && steps.finalize.outcome == 'success' && steps.finalize.outputs.has_state == 'true'"
        " && steps.publish.outputs.skip != 'true'"
    )
    assert record["with"]["name"] == "oracle-verdict-state-attempt-${{ steps.prepare.outputs.pr }}"
    assert record["with"]["name"] != _step(STATE_STEP)["with"]["name"]
    assert record["with"]["path"].endswith("oracle-final/attempt/state.json")
    assert record["with"]["overwrite"] is True and record["with"]["if-no-files-found"] == "ignore"
    assert "HAS_APP" not in record["if"]


@pytest.mark.parametrize(
    ("finalize_outcome", "has_state", "publish_outputs", "recorded"),
    [
        ("success", "true", {}, True),
        ("success", "true", {"skip": "false"}, True),
        ("success", "true", {"skip": "true"}, False),
        ("success", "false", {"skip": "false"}, False),
        ("failure", "true", {}, False),
    ],
)
def test_the_retry_attempt_is_recorded_unless_the_guard_skipped_or_nothing_was_finalized(
    finalize_outcome: str, has_state: str, publish_outputs: dict[str, str], recorded: bool
) -> None:
    condition = _step(RECORD_STEP)["if"].removeprefix("always() && ")
    context = {
        "steps": {
            "finalize": {"outcome": finalize_outcome, "outputs": {"has_state": has_state}},
            "publish": {"outputs": publish_outputs},
        }
    }

    assert evaluate(condition, context) is recorded


def test_the_retry_state_and_the_gate_run_only_after_a_successful_post() -> None:
    names = _names()
    state = _step(STATE_STEP)
    assert state["if"] == "steps.publish.outputs.state_recorded == 'true'"
    assert not re.search(r"always\(\)|failure\(\)|cancelled\(\)|success\(\)", state["if"])
    assert not re.search(r"always\(\)|failure\(\)|cancelled\(\)|success\(\)", _step(DISPATCH_STEP)["if"])
    assert names.index(STATE_STEP) > names.index(PUBLISH_STEP)
    assert state["with"]["name"] == "oracle-verdict-state-${{ steps.prepare.outputs.pr }}"
    assert state["with"]["retention-days"] == 7
    assert state["with"]["overwrite"] is True
    assert state["with"]["path"].endswith("oracle-final/state/state.json")
    assert state["with"]["if-no-files-found"] == "error"


def test_attempt_artifacts_keep_their_names_and_wait_for_the_finalize_step() -> None:
    names = _names()
    for slot in range(1, POLICY.max_attempts + 1):
        upload = _step(f"Upload attempt {slot}")
        assert upload["with"]["name"] == f"oracle-verdict-attempt-{slot}"
        assert upload["with"]["path"] == f"${{{{ runner.temp }}}}/oracle-attempts/attempt-{slot}.json"
        assert upload["with"]["retention-days"] == 7
        assert names.index(upload["name"]) > names.index(FINALIZE_STEP)
        assert upload["if"] == (
            f"${{{{ always() && steps.finalize.outcome == 'success' && steps.finalize.outputs.attempts >= {slot} }}}}"
        )
    upload = _step(CAPTURE_STEP)
    assert upload["with"]["name"] == "oracle-verdict-capture"
    assert upload["with"]["path"] == "${{ runner.temp }}/oracle-attempts/captures/"
    assert upload["with"]["retention-days"] == 30
    assert names.index(CAPTURE_STEP) > names.index(FINALIZE_STEP)
    assert (
        upload["if"] == "${{ always() && steps.finalize.outcome == 'success' && steps.finalize.outputs.attempts >= 1 }}"
    )


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


def test_no_step_lists_or_reruns_oracle_review_runs() -> None:
    text = _text_of_workflow()
    assert "oracle-review.yml" not in text
    assert "gh run list" not in text
    assert "gh run rerun" not in text


def test_only_sweep_and_review_hold_actions_write() -> None:
    holders = {name for name, job in _jobs().items() if job.get("permissions", {}).get("actions") == "write"}
    assert holders == {"sweep", "review"}


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
