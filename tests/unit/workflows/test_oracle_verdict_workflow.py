from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from _project.scripts.oracle_reviewers.commands import build
from _project.scripts.oracle_reviewers.policy import SIZE_BUCKETS, load_policy

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "oracle-verdict.yml"
LEGACY = REPO_ROOT / ".github" / "workflows" / "oracle-review.yml"
POLICY = load_policy(REPO_ROOT / ".github" / "oracle-reviewers.yml")
REVIEW_SECRETS = {"ORACLE_GATEWAY_TOKEN", "ORACLE_GATEWAY_URL", "TS_OAUTH_CLIENT_ID", "TS_AUDIENCE"}
APP_SECRETS = {"ORACLE_APP_ID", "ORACLE_APP_PRIVATE_KEY"}
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
SECRET_REF = re.compile(r"secrets\.([A-Z0-9_]+)")
OMP_SHA = re.compile(r"^[0-9a-f]{64}$")
DISPATCH_STEP = "Dispatch the gate for the reviewed head"


def _load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _jobs() -> dict[str, Any]:
    return _load()["jobs"]


def _text(value: Any) -> str:
    return yaml.safe_dump(value, sort_keys=True)


def _secrets(job: dict[str, Any]) -> set[str]:
    return set(SECRET_REF.findall(_text(job)))


def _attempt_jobs() -> dict[str, dict[str, Any]]:
    return {name: job for name, job in _jobs().items() if name.startswith("attempt-")}


def _select_jobs() -> dict[str, dict[str, Any]]:
    return {name: job for name, job in _jobs().items() if name.startswith("select-")}


def _run(job: dict[str, Any], step_name: str) -> str:
    return next(step["run"] for step in job["steps"] if step.get("name") == step_name)


def test_triggers() -> None:
    workflow = _load()
    assert workflow["name"] == "oracle-verdict"
    triggers = workflow[True] if True in workflow else workflow.get("on", {})
    assert set(triggers) == {"pull_request_target", "issue_comment", "workflow_dispatch", "schedule"}
    assert "pull_request" not in triggers


def test_existing_gate_is_untouched() -> None:
    legacy = yaml.safe_load(LEGACY.read_text(encoding="utf-8"))
    assert legacy["name"] == "oracle-review"
    assert "context: oracle-review\n" not in WORKFLOW.read_text(encoding="utf-8")
    assert list(legacy["jobs"]) == ["review-event"]


def test_workflow_permissions_are_empty_and_jobs_are_minimal() -> None:
    assert _load()["permissions"] == {}
    for name, job in _jobs().items():
        allowed = {"actions", "contents", "pull-requests"}
        if name.startswith("attempt-"):
            assert job["permissions"].get("id-token") == "write", name
            allowed = allowed | {"id-token"}
        assert set(job.get("permissions", {})) <= allowed, name


def test_attempt_jobs_join_the_tailnet_before_the_reviewer_runs() -> None:
    for name, job in _attempt_jobs().items():
        names = [step["name"] for step in job["steps"]]
        join = job["steps"][names.index("Join the tailnet to reach the credential gateway")]
        assert names.index("Join the tailnet to reach the credential gateway") < names.index("Run the reviewer"), name
        assert join["uses"].startswith("tailscale/github-action@"), name
        assert join["with"]["tags"] == "tag:oracle-ci", name
        assert "oauth-secret" not in join["with"] and "authkey" not in join["with"], name
        assert join["if"] == "env.HAS_TAILNET == 'true'", name


def test_attempt_jobs_write_the_reviewer_home_before_the_reviewer_runs() -> None:
    for name, job in _attempt_jobs().items():
        names = [step["name"] for step in job["steps"]]
        home = names.index("Write the reviewer's OMP home")
        assert names.index("Check out the trusted develop copy") < home < names.index("Run the reviewer"), name
        step = job["steps"][home]
        assert step["working-directory"] == "trusted", name
        assert step["env"] == {"ORACLE_GATEWAY_URL": "${{ secrets.ORACLE_GATEWAY_URL }}"}, name
        assert 'reviewer-home --home "$HOME" --gateway-url "$ORACLE_GATEWAY_URL"' in step["run"], name


def test_attempt_jobs_hold_only_the_gateway_token_and_no_provider_credentials() -> None:
    for name, job in _attempt_jobs().items():
        step = next(step for step in job["steps"] if step["name"] == "Run the reviewer")
        assert set(step["env"]) == {"OMP_GATEWAY_TOKEN", "REVIEWER"}, name
        assert step["env"]["OMP_GATEWAY_TOKEN"] == "${{ secrets.ORACLE_GATEWAY_TOKEN }}", name
        text = _text(job)
        for gone in (
            "OMP_AGENT_CONFIG",
            "OMP_AUTH_BROKER",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "OPENAI_API_KEY",
            "META_API_KEY",
            "GEMINI_API_KEY",
        ):
            assert gone not in text, (name, gone)


def test_attempt_jobs_check_out_the_committed_reviewer_config() -> None:
    for name, job in _attempt_jobs().items():
        trusted = next(step for step in job["steps"] if step["name"] == "Check out the trusted develop copy")
        assert ".github/oracle-omp-config.yml" in trusted["with"]["sparse-checkout"].split(), name


def test_concurrency_is_per_pull_request_and_ignores_unrelated_events() -> None:
    concurrency = _load()["concurrency"]
    group = concurrency["group"]
    assert "oracle-verdict-" in group
    assert "github.event.changes.base" in group


def test_attempt_jobs_rely_on_the_per_pull_request_group_alone() -> None:
    for name, job in _attempt_jobs().items():
        assert "concurrency" not in job, name


def test_plan_guards_comments_and_edits() -> None:
    condition = _jobs()["plan"]["if"]
    assert "issue_comment" in condition
    assert "author_association" in condition
    assert "review_comment" not in condition
    assert _jobs()["sweep"]["if"] == "github.event_name == 'schedule'"


def test_dispatch_is_refused_outside_develop() -> None:
    plan = _jobs()["plan"]
    guard = next(step for step in plan["steps"] if step["name"] == "Refuse a dispatch from any ref other than develop")
    assert guard["env"]["CURRENT_REF"] == "${{ github.ref }}"
    assert "exit 2" in guard["run"]


def test_plan_job_holds_no_secret_and_resolves_the_head_by_api() -> None:
    plan = _jobs()["plan"]
    assert _secrets(plan) == set()
    run = _run(plan, "Plan the review")
    assert "cli plan" in run
    upload = next(step for step in plan["steps"] if step.get("name") == "Upload the plan")
    assert upload["with"]["name"] == "oracle-verdict-plan"


def test_reviewer_slots_cover_the_policy_and_run_sequentially() -> None:
    jobs = _jobs()
    slots = POLICY.max_attempts
    assert {f"select-{slot}" for slot in range(1, slots + 1)} <= set(jobs)
    assert set(_attempt_jobs()) == {f"attempt-{slot}-omp" for slot in range(1, slots + 1)}
    for name, job in jobs.items():
        assert "strategy" not in job, name
    assert jobs["select-1"]["needs"] == "plan"
    for slot in range(2, slots + 1):
        needs = set(jobs[f"select-{slot}"]["needs"])
        assert {f"select-{slot - 1}", f"attempt-{slot - 1}-omp"} <= needs
        assert f"needs.select-{slot - 1}.outputs.reviewer != ''" in jobs[f"select-{slot}"]["if"]
    for name, job in _attempt_jobs().items():
        slot = name.split("-")[1]
        assert job["needs"] == ["plan", f"select-{slot}"]
        assert job["if"] == f"${{{{ !cancelled() && needs.select-{slot}.outputs.reviewer != '' }}}}"


def test_every_bucket_reviewer_resolves_and_selects_cover_all_buckets() -> None:
    assert set(POLICY.reviewers) == set(SIZE_BUCKETS)
    assert set(POLICY.buckets) == set(SIZE_BUCKETS)
    for bucket in SIZE_BUCKETS:
        assert POLICY.reviewers[bucket].harness == "omp"


def test_classification_flows_from_select_1_to_every_later_job() -> None:
    jobs = _jobs()
    upload = next(step for step in jobs["select-1"]["steps"] if step.get("name") == "Upload the classification")
    assert upload["with"]["name"] == "oracle-verdict-classification"
    assert upload["with"]["if-no-files-found"] == "warn"
    for slot in range(2, POLICY.max_attempts + 1):
        download = next(
            step for step in jobs[f"select-{slot}"]["steps"] if step.get("name") == "Download the classification"
        )
        assert download["with"]["name"] == "oracle-verdict-classification"
    for job in _attempt_jobs().values():
        download = next(step for step in job["steps"] if step.get("name") == "Download the classification")
        assert download["with"]["name"] == "oracle-verdict-classification"
        assert "--classification " in _run(job, "Run the reviewer")


def test_only_select_1_holds_the_judgment_key() -> None:
    for name, job in _select_jobs().items():
        if name == "select-1":
            assert _secrets(job) == {"TYPESAFE_API_KEY"}, name
        else:
            assert _secrets(job) == set(), name
    assert _jobs()["select-1"]["environment"] == "oracle"


def test_each_reviewer_job_loads_only_provider_secrets() -> None:
    for name, job in _attempt_jobs().items():
        assert job["environment"] == "oracle", name
        assert _secrets(job) == REVIEW_SECRETS, name


def test_only_the_post_job_holds_the_app_key() -> None:
    for name, job in _jobs().items():
        secrets = _secrets(job)
        if name == "post":
            assert secrets == APP_SECRETS
        else:
            assert not secrets & APP_SECRETS, name
    assert _jobs()["post"]["environment"] == "oracle"


def test_reviewer_jobs_never_execute_pull_request_code() -> None:
    for name, job in _attempt_jobs().items():
        checkouts = [step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")]
        trusted, head = checkouts
        assert trusted["with"]["ref"] == "${{ github.sha }}"
        assert trusted["with"]["path"] == "trusted"
        assert head["with"]["ref"] == "${{ needs.plan.outputs.head_sha }}"
        assert head["with"]["path"] == "workspace"
        assert head["with"]["persist-credentials"] is False
        for step in job["steps"]:
            if "run" in step:
                assert step.get("working-directory") in (None, "trusted"), (name, step["name"])
                assert "bash -c" not in step["run"]
                assert "workspace/" not in step["run"].replace("$GITHUB_WORKSPACE/workspace", "")
        run = _run(job, "Run the reviewer")
        assert "_project.scripts.oracle_reviewers.cli review" in run
        assert "--harness omp" in run


def test_every_checkout_disables_persisted_credentials() -> None:
    for name, job in _jobs().items():
        for step in job["steps"]:
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False, name


def test_omp_installs_are_pinned_and_checked() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "npm install" not in text
    assert "setup-node" not in text
    assert "dev.meta.ai" not in text
    assert "antigravity" not in text
    assert "codex login" not in text
    assert "sysctl" not in text
    assert "CODEX_HOME" not in text
    assert "AGY_CLI_DISABLE_AUTO_UPDATE" not in text
    for name, job in _attempt_jobs().items():
        install = next(step for step in job["steps"] if step["name"] == "Install omp")
        assert install["env"]["OMP_URL"].startswith("https://build.stencil.so/d/omp/")
        assert OMP_SHA.match(install["env"]["OMP_SHA256"]), name
        lines = install["run"].splitlines()
        check = 'echo "$OMP_SHA256  $RUNNER_TEMP/omp" | sha256sum --check --strict'
        assert lines.index(check) < lines.index('chmod +x "$RUNNER_TEMP/omp"')
        assert "env" not in install or set(install["env"]) == {"OMP_URL", "OMP_SHA256"}
    assert text.count("OMP_SHA256: ") == POLICY.max_attempts
    curl = "curl -fsSL --proto '=https' --tlsv1.2 \"$OMP_URL\""
    assert "curl " not in text.replace(curl, "")
    assert "--dangerously" not in text and "--yolo" not in text


def test_reviewer_attempts_always_leave_an_artifact() -> None:
    for name, job in _attempt_jobs().items():
        ensure = next(
            step for step in job["steps"] if step["name"] == "Record an absence if the reviewer did not finish"
        )
        upload = next(step for step in job["steps"] if step["name"] == "Upload the attempt")
        assert ensure["if"] == "always()" and upload["if"] == "always()"
        assert upload["with"]["name"] == f"oracle-verdict-attempt-{name.split('-')[1]}"


def test_post_job_mints_the_app_token_and_tolerates_missing_secrets() -> None:
    post = _jobs()["post"]
    assert post["if"] == "${{ !cancelled() && needs.plan.result == 'success' && needs.plan.outputs.post == 'true' }}"
    assert post["env"]["HAS_APP"] == "${{ secrets.ORACLE_APP_ID != '' && secrets.ORACLE_APP_PRIVATE_KEY != '' }}"
    mint = next(step for step in post["steps"] if step.get("id") == "app")
    assert mint["uses"] == "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
    assert mint["if"] == "env.HAS_APP == 'true'"
    assert mint["with"]["permission-statuses"] == "write"
    assert mint["with"]["permission-pull-requests"] == "write"
    absent = next(step for step in post["steps"] if step["name"] == "Log the result when the App is not configured")
    assert absent["if"] == "env.HAS_APP != 'true'"
    for step in post["steps"]:
        if "GH_TOKEN" in step.get("env", {}):
            if step["name"] == DISPATCH_STEP:
                assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
                assert step["env"]["READ_TOKEN"] == "${{ steps.app.outputs.token }}"
                assert step["if"].startswith("env.HAS_APP == 'true'")
            else:
                assert step["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"
                assert step["if"].startswith("env.HAS_APP == 'true'")
    finalize = _run(post, "Validate this run's artifacts and decide")
    assert "cli finalize" in finalize
    downloads = [step for step in post["steps"] if str(step.get("uses", "")).startswith("actions/download-artifact@")]
    assert all("run-id" not in step["with"] for step in downloads)


def test_status_is_posted_on_the_api_resolved_head() -> None:
    post = _jobs()["post"]
    assert post["env"]["HEAD_SHA"] == "${{ needs.plan.outputs.head_sha }}"
    assert "repos/$REPO/statuses/$HEAD_SHA" in _run(post, "Post the commit status")


def test_actions_are_pinned_to_full_shas() -> None:
    for name, job in _jobs().items():
        for step in job["steps"]:
            if "uses" in step:
                assert SHA_PIN.match(step["uses"]), (name, step["uses"])


def test_workflow_has_no_comments() -> None:
    assert not any(line.lstrip().startswith("#") for line in WORKFLOW.read_text(encoding="utf-8").splitlines())


def test_reviewer_commands_keep_read_only_flags(tmp_path: Path) -> None:
    for name, reviewer in POLICY.reviewers.items():
        argv = build(reviewer, tmp_path, "prompt", tmp_path).argv
        assert argv[argv.index("--tools") + 1] == "read,grep,glob", name
        assert argv[argv.index("--approval-mode") + 1] == "always-ask", name
        assert reviewer.model in argv, name


def test_the_post_job_rechecks_the_review_list_before_posting() -> None:
    post = _jobs()["post"]
    names = [step["name"] for step in post["steps"]]
    assert "Skip a result this head already has" in names
    for name in ("Post the commit status", "Post the findings comment", "Post the findings review"):
        step = next(step for step in post["steps"] if step["name"] == name)
        assert "steps.guard.outputs.skip != 'true'" in step["if"], name


def test_no_step_lists_or_reruns_oracle_review_runs() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "oracle-review.yml" not in text
    assert "gh run list" not in text
    assert "gh run rerun" not in text
    for job in _jobs().values():
        assert "Rerun the failed synchronize suite for this head" not in [step.get("name") for step in job["steps"]]


def test_only_sweep_and_post_hold_actions_write() -> None:
    assert _jobs()["post"]["permissions"] == {"actions": "write", "contents": "read"}
    holders = {name for name, job in _jobs().items() if job.get("permissions", {}).get("actions") == "write"}
    assert holders == {"sweep", "post"}


def test_post_dispatches_the_gate_last_and_only_after_the_review_is_posted() -> None:
    steps = _jobs()["post"]["steps"]
    names = [step["name"] for step in steps]
    assert names[-1] == DISPATCH_STEP
    assert names.index(DISPATCH_STEP) > names.index("Post the findings review")
    assert names.index(DISPATCH_STEP) > names.index("Post the commit status")
    assert names.index(DISPATCH_STEP) > names.index("Post the findings comment")
    step = steps[-1]
    assert step["if"] == "env.HAS_APP == 'true' && steps.guard.outputs.skip != 'true'"


def test_post_dispatch_targets_develop_with_the_reviewed_head() -> None:
    run = _run(_jobs()["post"], DISPATCH_STEP)
    assert 'gh api "repos/$REPO/pulls/$PR_NUMBER" --jq .base.ref' in run
    assert 'if [ "$base" = develop ]; then' in run
    assert (
        'gh workflow run oracle-gate.yml --repo "$REPO" --ref develop -f pr="$PR_NUMBER" -f head_sha="$HEAD_SHA"' in run
    )
    assert run.index("if [") < run.index("gh workflow run")
    assert "statuses/" not in run


def test_only_the_post_job_dispatches_the_gate() -> None:
    dispatchers = [
        (name, step["name"])
        for name, job in _jobs().items()
        for step in job["steps"]
        if "gh workflow run oracle-gate.yml" in step.get("run", "")
    ]
    assert dispatchers == [("post", DISPATCH_STEP)]


def test_post_waits_for_every_slot() -> None:
    needs = set(_jobs()["post"]["needs"])
    slots = POLICY.max_attempts
    assert {f"select-{slot}" for slot in range(1, slots + 1)} <= needs
    assert {f"attempt-{slot}-omp" for slot in range(1, slots + 1)} <= needs
