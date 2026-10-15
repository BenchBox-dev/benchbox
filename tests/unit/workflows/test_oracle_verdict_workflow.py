from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from _project.scripts.oracle_reviewers.commands import build
from _project.scripts.oracle_reviewers.policy import load_policy

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "oracle-verdict.yml"
LEGACY = REPO_ROOT / ".github" / "workflows" / "oracle-review.yml"
POLICY = load_policy(REPO_ROOT / ".github" / "oracle-reviewers.yml")
HARNESS_SECRETS = {
    "claude": {"CLAUDE_CODE_OAUTH_TOKEN"},
    "codex": {"OPENAI_API_KEY"},
    "muse": {"META_API_KEY"},
    "agy": {"GEMINI_API_KEY"},
}
AGY_TARBALL = "https://storage.googleapis.com/antigravity-public/antigravity-cli/1.3.3-5524738307653632/linux-x64/cli_linux_x64.tar.gz"
APP_SECRETS = {"ORACLE_APP_ID", "ORACLE_APP_PRIVATE_KEY"}
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
SECRET_REF = re.compile(r"secrets\.([A-Z0-9_]+)")


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


def _harness(name: str) -> str:
    return name.rsplit("-", 1)[1]


def _run(job: dict[str, Any], step_name: str) -> str:
    return next(step["run"] for step in job["steps"] if step.get("name") == step_name)


def test_triggers() -> None:
    workflow = _load()
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"pull_request_target", "issue_comment", "workflow_dispatch", "schedule"}
    assert triggers["pull_request_target"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
        "edited",
    ]
    assert triggers["issue_comment"]["types"] == ["created"]
    assert triggers["workflow_dispatch"]["inputs"]["pr"]["required"] is True
    assert len(triggers["schedule"]) == 1
    assert "pull_request" not in triggers


def test_existing_gate_is_untouched() -> None:
    legacy = yaml.safe_load(LEGACY.read_text(encoding="utf-8"))
    assert legacy["jobs"]["oracle-review"]["name"] == "oracle-review"
    assert POLICY.status_context == "oracle-verdict"
    assert "context: oracle-review\n" not in WORKFLOW.read_text(encoding="utf-8")


def test_workflow_permissions_are_empty_and_jobs_are_minimal() -> None:
    assert _load()["permissions"] == {}
    jobs = _jobs()
    assert jobs["plan"]["permissions"] == {"actions": "read", "contents": "read", "pull-requests": "read"}
    assert jobs["sweep"]["permissions"] == {"actions": "write", "contents": "read", "pull-requests": "read"}
    assert jobs["post"]["permissions"] == {"actions": "write", "contents": "read"}
    for name, job in jobs.items():
        if name.startswith(("select-", "attempt-")):
            assert job["permissions"] == {"contents": "read"}, name
    assert "id-token" not in WORKFLOW.read_text(encoding="utf-8")


def test_concurrency_is_per_pull_request_and_ignores_unrelated_events() -> None:
    concurrency = _load()["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    group = concurrency["group"]
    assert "format('pr-{0}', github.event.pull_request.number || github.event.issue.number || inputs.pr)" in group
    assert "format('run-{0}', github.run_id)" in group
    assert "startsWith(github.event.comment.body, '/oracle-review')" in group
    assert "github.event.changes.base" in group


def test_attempt_jobs_rely_on_the_per_pull_request_group_alone() -> None:
    for name, job in _attempt_jobs().items():
        assert "concurrency" not in job, name


def test_plan_guards_comments_and_edits() -> None:
    condition = _jobs()["plan"]["if"]
    assert "startsWith(github.event.comment.body, '/oracle-review')" in condition
    assert "github.event.issue.pull_request" in condition
    assert (
        'contains(fromJSON(\'["OWNER", "MEMBER", "COLLABORATOR"]\'), github.event.comment.author_association)'
        in condition
    )
    assert "github.event.action != 'edited' || github.event.changes.base" in condition
    assert "schedule" not in condition
    assert _jobs()["sweep"]["if"] == "github.event_name == 'schedule'"


def test_dispatch_is_refused_outside_develop() -> None:
    plan = _jobs()["plan"]
    guard = next(step for step in plan["steps"] if step["name"] == "Refuse a dispatch from any ref other than develop")
    assert guard["if"] == "github.event_name == 'workflow_dispatch'"
    assert guard["env"] == {"CURRENT_REF": "${{ github.ref }}"}
    assert '"$CURRENT_REF" != refs/heads/develop' in guard["run"]
    assert "exit 2" in guard["run"]


def test_plan_job_holds_no_secret_and_resolves_the_head_by_api() -> None:
    plan = _jobs()["plan"]
    assert _secrets(plan) == set()
    assert "environment" not in plan
    run = _run(plan, "Plan the review")
    assert "_project.scripts.oracle_reviewers.cli plan" in run
    assert plan["outputs"]["head_sha"] == "${{ steps.plan.outputs.head_sha }}"
    upload = next(step for step in plan["steps"] if step["name"] == "Upload the plan")
    assert upload["with"]["name"] == "oracle-verdict-plan"


def test_reviewer_slots_cover_the_policy_and_run_sequentially() -> None:
    jobs = _jobs()
    slots = POLICY.max_attempts
    assert {f"select-{slot}" for slot in range(1, slots + 1)} <= set(jobs)
    assert set(_attempt_jobs()) == {f"attempt-{slot}-{h}" for slot in range(1, slots + 1) for h in HARNESS_SECRETS}
    for name, job in jobs.items():
        assert "strategy" not in job, name
    assert jobs["select-1"]["needs"] == "plan"
    for slot in range(2, slots + 1):
        needs = set(jobs[f"select-{slot}"]["needs"])
        assert {f"select-{slot - 1}", *(f"attempt-{slot - 1}-{h}" for h in HARNESS_SECRETS)} <= needs
        assert f"needs.select-{slot - 1}.outputs.reviewer != ''" in jobs[f"select-{slot}"]["if"]
    for name, job in _attempt_jobs().items():
        slot = name.split("-")[1]
        assert job["needs"] == ["plan", f"select-{slot}"]
        assert job["if"] == f"${{{{ !cancelled() && needs.select-{slot}.outputs.harness == '{_harness(name)}' }}}}"


def test_every_enabled_harness_has_jobs() -> None:
    enabled = {reviewer.harness for reviewer in POLICY.reviewers.values() if reviewer.enabled}
    assert enabled == set(HARNESS_SECRETS)
    assert {_harness(name) for name in _attempt_jobs()} == enabled


def test_each_reviewer_job_loads_only_its_own_secret() -> None:
    for name, job in _attempt_jobs().items():
        assert job["environment"] == "oracle", name
        assert _secrets(job) == HARNESS_SECRETS[_harness(name)], name


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
        assert f"--harness {_harness(name)}" in run


def test_every_checkout_disables_persisted_credentials() -> None:
    for name, job in _jobs().items():
        for step in job["steps"]:
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False, name


def test_cli_installs_come_from_official_sources_with_pins() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "npm install --global @anthropic-ai/claude-code@2.1.289" in text
    assert "npm install --global @openai/codex@0.160.0" in text
    assert text.count("https://dev.meta.ai/install.sh") == POLICY.max_attempts
    assert text.count(f"AGY_URL: {AGY_TARBALL}") == POLICY.max_attempts
    muse_curl = "curl -fsSL --proto '=https' --tlsv1.2 https://dev.meta.ai/install.sh"
    agy_curl = "curl -fsSL --proto '=https' --tlsv1.2 \"$AGY_URL\""
    assert "curl " not in text.replace(muse_curl, "").replace(agy_curl, "")
    for name, job in _jobs().items():
        job_text = _text(job)
        if name.startswith("attempt-") and _harness(name) == "muse":
            install = next(step for step in job["steps"] if step["name"] == "Install muse")
            assert install["run"].startswith("curl -fsSL --proto '=https' --tlsv1.2 https://dev.meta.ai/install.sh -o ")
            lines = install["run"].splitlines()
            version = 'echo "Installed $("$HOME/.local/bin/muse" --version)" | tee -a "$GITHUB_STEP_SUMMARY"'
            assert lines.index('bash "$RUNNER_TEMP/muse-install.sh"') < lines.index(version)
            assert "env" not in install
            assert not any(line.strip().startswith("sh ") for line in install["run"].splitlines())
            assert job["permissions"] == {"contents": "read"}
            assert _secrets(job) == {"META_API_KEY"}
            assert "--harness muse" in _run(job, "Run the reviewer")
            for pinned in ("MUSE_INSTALLER_SHA256", "MUSE_NO_AUTO_UPDATE", "MUSE_CHANNEL", "sha256sum"):
                assert pinned not in job_text, name
        else:
            assert "dev.meta.ai" not in job_text and "META_API_KEY" not in job_text, name
    assert "printenv OPENAI_API_KEY | codex login --with-api-key" in text
    assert "--dangerously" not in text and "--yolo" not in text


def test_agy_installs_a_pinned_checksummed_release_with_api_key_auth() -> None:
    for name, job in _attempt_jobs().items():
        if _harness(name) != "agy":
            assert "GEMINI_API_KEY" not in _text(job) and "antigravity" not in _text(job), name
            continue
        install = next(step for step in job["steps"] if step["name"] == "Install agy")
        assert install["env"]["AGY_URL"] == AGY_TARBALL
        assert re.fullmatch(r"[0-9a-f]{64}", install["env"]["AGY_SHA256"])
        lines = install["run"].splitlines()
        check = 'echo "$AGY_SHA256  $RUNNER_TEMP/agy.tar.gz" | sha256sum --check --strict'
        assert lines.index(check) < next(index for index, line in enumerate(lines) if line.startswith("tar "))
        settings = 'echo \'{"modelProvider": "gemini"}\' > "$HOME/.gemini/antigravity-cli/settings.json"'
        assert settings in lines
        run = next(step for step in job["steps"] if step["name"] == "Run the reviewer")
        assert run["env"]["GEMINI_API_KEY"] == "${{ secrets.GEMINI_API_KEY }}"
        assert run["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "1"


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
            if step["name"] == "Rerun the failed synchronize suite for this head":
                assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
                assert step["if"] == "env.HAS_APP == 'true'"
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


READ_ONLY_FLAGS = {
    "claude": ("--tools", "Read,Grep,Glob"),
    "codex": ("--sandbox", "read-only"),
    "muse": ("--disable-write", "--disable-shell"),
    "agy": ("--mode", "plan"),
}
MODEL_PINS = {
    "opus": ("claude-opus-5-5", "medium"),
    "sonnet": ("claude-sonnet-5-5", "medium"),
    "sol": ("gpt-6.1-sol", "medium"),
    "luna": ("gpt-6-luna", "high"),
    "muse": ("muse-spark-1.3-contributor", "medium"),
    "agy": ("gemini-3.8-flash-medium", "medium"),
}


def test_reviewer_commands_keep_read_only_flags_and_model_pins(tmp_path: Path) -> None:
    for name, reviewer in POLICY.reviewers.items():
        assert (reviewer.model, reviewer.effort) == MODEL_PINS[name]
        argv = build(reviewer, tmp_path, "prompt", tmp_path).argv
        flags = READ_ONLY_FLAGS[reviewer.harness]
        start = argv.index(flags[0])
        assert argv[start : start + len(flags)] == flags, name
        assert reviewer.model in argv or f"--model={reviewer.model}" in argv, name


def test_the_post_job_rechecks_the_review_list_before_posting() -> None:
    post = _jobs()["post"]
    names = [step["name"] for step in post["steps"]]
    guard = next(step for step in post["steps"] if step.get("id") == "guard")
    assert names.index("Mint the App token") < names.index(guard["name"]) < names.index("Post the commit status")
    assert guard["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"
    assert "oracle_reviewers.cli guard --plan" in guard["run"]
    for name in ("Post the commit status", "Post the findings comment", "Post the findings review"):
        step = next(item for item in post["steps"] if item["name"] == name)
        assert "steps.guard.outputs.skip != 'true'" in step["if"], name


def test_post_job_reruns_the_failed_synchronize_suite_for_the_head() -> None:
    post = _jobs()["post"]
    names = [step["name"] for step in post["steps"]]
    step = next(item for item in post["steps"] if item["name"] == "Rerun the failed synchronize suite for this head")
    assert names.index("Post the findings review") < names.index(step["name"])
    assert step["if"] == "env.HAS_APP == 'true'"
    assert "gh run list" in step["run"]
    assert "--workflow oracle-review.yml" in step["run"]
    assert '.event == "pull_request"' in step["run"]
    assert '.conclusion == "failure"' in step["run"]
    assert "env.HEAD_SHA" in step["run"]
    assert 'gh run rerun "$id" --failed --repo "$REPO"' in step["run"]
    assert "set -euo pipefail" in step["run"]


def test_only_codex_jobs_let_the_sandbox_create_user_namespaces() -> None:
    step_name = "Let the Codex sandbox create user namespaces"
    codex_jobs = 0
    for name, job in _jobs().items():
        names = [step["name"] for step in job.get("steps", []) if "name" in step]
        if name.startswith("attempt-") and _harness(name) == "codex":
            codex_jobs += 1
            step = next(step for step in job["steps"] if step.get("name") == step_name)
            assert step["run"] == "sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0"
            assert names.index("Install Codex") < names.index(step_name) < names.index("Run the reviewer")
        else:
            assert "sudo " not in _text(job), name
    assert codex_jobs == POLICY.max_attempts
