from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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


def test_the_workflow_runs_daily_on_demand_and_after_ci_and_review_with_dry_run_on_by_default() -> None:
    triggers = load()[True]
    assert set(triggers) == {"schedule", "workflow_dispatch", "workflow_run"}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["default"] is True
    assert inputs["retry"]["default"] is False
    assert triggers["workflow_run"]["types"] == ["completed"]


def test_every_workflow_that_triggers_arming_exists_by_that_name() -> None:
    names = {yaml.safe_load(path.read_text(encoding="utf-8")).get("name") for path in WORKFLOWS.glob("*.yml")}
    assert set(load()[True]["workflow_run"]["workflows"]) <= names


@pytest.mark.parametrize("name", ["readiness", "prepare", "publish", "arm"])
def test_every_job_runs_only_in_the_main_repository_on_develop(name: str) -> None:
    condition = job(name)["if"]
    assert "github.repository == 'BenchBox-dev/BenchBox'" in condition
    assert "github.ref == 'refs/heads/develop'" in condition


@pytest.mark.parametrize(
    ("event", "expected"), [("schedule", True), ("workflow_dispatch", True), ("workflow_run", False)]
)
def test_app_readiness_runs_on_the_schedule_and_dispatch_never_on_completion_events(event: str, expected: bool) -> None:
    from tests.unit.workflows.workflow_expressions import evaluate

    context = {"github": {"repository": "BenchBox-dev/BenchBox", "ref": "refs/heads/develop", "event_name": event}}
    assert bool(evaluate(job("readiness")["if"], context)) is expected


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
    assert job("publish")["needs"] == ["prepare", "readiness"]


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
    ("case", "message"),
    [
        ("ready", "Verified configured App authentication"),
        ("missing", "Configure SQLGLOT_DELIVERY_APP_ID"),
        ("bad-key", "private key is invalid"),
        ("uninstalled", "HTTP 404"),
        ("wrong-app", "missing or suspended"),
        ("suspended", "missing or suspended"),
        ("all-repositories", "selected BenchBox-dev repositories"),
        ("extra-permission", "only contents/PR write"),
        ("missing-permission", "only contents/PR write"),
        ("wrong-repository", "installed on BenchBox only"),
        ("extra-repository", "installed on BenchBox only"),
        ("unreadable-repositories", "HTTP 403"),
    ],
)
def test_readiness_authenticates_without_repository_mutations(tmp_path: Path, case: str, message: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    script = job("readiness")["steps"][0]["run"]
    probe = "\n".join(script.splitlines()[1:-1])
    prefix = """
const scenario = process.env.PROBE_SCENARIO;
const fixtureCrypto = require('node:crypto');
process.env.APP_ID = scenario === 'missing' ? '' : '42';
process.env.APP_KEY = scenario === 'bad-key' ? 'invalid' : fixtureCrypto.generateKeyPairSync('rsa', {
  modulusLength: 2048, privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
  publicKeyEncoding: { type: 'spki', format: 'pem' },
}).privateKey;
const calls = [];
globalThis.fetch = async (url, options) => {
  calls.push([options.method, new URL(url).pathname]);
  require('node:fs').writeFileSync(process.env.PROBE_CALLS, JSON.stringify(calls));
  let value;
  let status = 200;
  if (url.endsWith('/installation')) {
    value = { id: 7, app_id: 42, account: { login: 'BenchBox-dev' }, repository_selection: 'selected',
      permissions: { contents: 'write', pull_requests: 'write', metadata: 'read' } };
    if (scenario === 'uninstalled') status = 404;
    if (scenario === 'wrong-app') value.app_id = 99;
    if (scenario === 'suspended') value.suspended_at = '2026-01-01';
    if (scenario === 'all-repositories') value.repository_selection = 'all';
    if (scenario === 'extra-permission') value.permissions.actions = 'write';
    if (scenario === 'missing-permission') delete value.permissions.contents;
  } else if (url.endsWith('/access_tokens')) {
    const requested = JSON.parse(options.body).permissions;
    if (requested.contents !== 'read' || requested.pull_requests !== 'read')
      throw new Error('probe requested a writable token');
    value = { token: 'temporary-read-token' };
  } else if (options.method === 'DELETE') {
    status = 204;
  } else {
    value = { total_count: 1, repositories: [{ full_name: 'BenchBox-dev/BenchBox' }] };
    if (scenario === 'wrong-repository') value.repositories[0].full_name = 'BenchBox-dev/benchbox-sqlglot';
    if (scenario === 'extra-repository') value.total_count = 2;
    if (scenario === 'unreadable-repositories') status = 403;
  }
  return { ok: status >= 200 && status < 300, status, json: async () => value };
};
"""
    summary = tmp_path / "summary.md"
    calls_path = tmp_path / "calls.json"
    result = subprocess.run(
        [node, "-"],
        input=prefix + probe,
        text=True,
        encoding="utf-8",
        capture_output=True,
        env={
            **os.environ,
            "PROBE_SCENARIO": case,
            "PROBE_CALLS": str(calls_path),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        timeout=20,
    )
    assert (result.returncode == 0) is (case == "ready"), result.stderr
    assert message in summary.read_text(encoding="utf-8")
    calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
    assert all(
        method == "GET" or path in {"/app/installations/7/access_tokens", "/installation/token"}
        for method, path in calls
    )
    if case in {"ready", "wrong-repository", "extra-repository", "unreadable-repositories"}:
        assert calls[-1] == ["DELETE", "/installation/token"]


ADOPTION = "chore/sqlglot-30.30.0.1"


@pytest.mark.parametrize(
    ("event", "conclusion", "source_repo", "enabled", "ref", "branch", "expected"),
    [
        ("workflow_run", "success", "BenchBox-dev/BenchBox", "true", "refs/heads/develop", ADOPTION, True),
        ("workflow_run", "failure", "BenchBox-dev/BenchBox", "true", "refs/heads/develop", ADOPTION, False),
        ("workflow_run", "success", "outsider/BenchBox", "true", "refs/heads/develop", ADOPTION, False),
        ("workflow_run", "success", "BenchBox-dev/BenchBox", "false", "refs/heads/develop", ADOPTION, False),
        ("workflow_run", "success", "BenchBox-dev/BenchBox", "", "refs/heads/develop", ADOPTION, False),
        ("workflow_dispatch", "success", "BenchBox-dev/BenchBox", "true", "refs/heads/develop", ADOPTION, False),
        ("workflow_run", "success", "BenchBox-dev/BenchBox", "true", "refs/heads/untrusted", ADOPTION, False),
        ("workflow_run", "success", "BenchBox-dev/BenchBox", "true", "refs/heads/develop", "feat/unrelated", False),
        ("workflow_run", "success", "BenchBox-dev/BenchBox", "true", "refs/heads/develop", "develop", False),
    ],
)
def test_completion_arming_refuses_untrusted_events_and_disabled_runs(
    event: str, conclusion: str, source_repo: str, enabled: str, ref: str, branch: str, expected: bool
) -> None:
    from tests.unit.workflows.workflow_expressions import evaluate

    workflow_run = {"conclusion": conclusion, "head_repository": {"full_name": source_repo}, "head_branch": branch}
    context = {
        "github": {
            "repository": "BenchBox-dev/BenchBox",
            "ref": ref,
            "event_name": event,
            "event": {"workflow_run": workflow_run},
        },
        "vars": {"SQLGLOT_DELIVERY_ENABLED": enabled},
    }
    assert bool(evaluate(job("arm")["if"], context)) is expected


def test_the_arm_branch_filter_matches_the_trust_root_branch_prefix() -> None:
    trust_root = json.loads((WORKFLOWS.parent / "sqlglot-delivery.json").read_text(encoding="utf-8"))
    prefix = trust_root["consumer"]["branchPrefix"]
    assert f"startsWith(github.event.workflow_run.head_branch, '{prefix}')" in job("arm")["if"]


def test_the_readiness_job_is_isolated_from_the_repository_and_prepared_artifacts() -> None:
    readiness = job("readiness")
    assert readiness["permissions"] == {}
    assert "needs" not in readiness
    assert not used(readiness)
    assert "app-token" not in job_text("readiness")
