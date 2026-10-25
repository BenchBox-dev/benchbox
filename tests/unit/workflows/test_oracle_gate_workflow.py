from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "oracle-gate.yml"
VERDICT = REPO_ROOT / ".github" / "workflows" / "oracle-verdict.yml"
SCRIPT = "_project/scripts/oracle_gate.py"
CHECKER_PATH = REPO_ROOT / "_project/scripts/oracle_review_check.py"


def _script_module(path: Path, name: str) -> Any:
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def _attested_shas(comment: dict[str, Any]) -> list[str]:
    return _script_module(CHECKER_PATH, "oracle_review_check_under_workflow_test")._attested_shas(comment)


SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
KNOWN_SHAS = {
    match.group(0).split("@")[1]
    for match in re.finditer(
        r"[\w.-]+/[\w.-]+@([0-9a-f]{40})", (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
} | {
    "fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",
    "bcd2ba49218906704ab6c1aa796996da409d3eb1",
}


def _load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers() -> dict[str, Any]:
    workflow = _load()
    return workflow[True] if True in workflow else workflow.get("on", {})


def _jobs() -> dict[str, Any]:
    return _load()["jobs"]


def _steps(name: str) -> list[dict[str, Any]]:
    return _jobs()[name]["steps"]


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_triggers_are_trusted_only() -> None:
    triggers = _triggers()
    assert set(triggers) == {
        "push",
        "pull_request_target",
        "issue_comment",
        "workflow_run",
        "workflow_dispatch",
        "schedule",
    }
    assert triggers["push"]["branches"] == ["develop"]
    assert set(triggers["push"]) == {"branches", "paths"}
    assert "pull_request" not in triggers
    assert "pull_request_review" not in triggers


def _sparse_entries(job: str) -> list[str]:
    checkouts = [step for step in _steps(job) if "actions/checkout" in step.get("uses", "")]
    assert len(checkouts) == 1
    return checkouts[0]["with"]["sparse-checkout"].split()


def _normalized(entries: list[str]) -> list[str]:
    return [f"{entry}/**" if (REPO_ROOT / entry).is_dir() else entry for entry in entries]


def test_push_paths_equal_the_sparse_checkout_policy_inputs() -> None:
    paths = _triggers()["push"]["paths"]
    assert "_project/scripts/oracle_reviewers/**" in paths
    for job in ("gate", "sweep"):
        assert sorted(paths) == sorted(_normalized(_sparse_entries(job)))
    assert len(paths) == len(set(paths))


def test_pull_request_target_covers_open_push_reopen_ready_and_edit() -> None:
    types = _triggers()["pull_request_target"]["types"]
    assert set(types) == {"opened", "synchronize", "reopened", "ready_for_review", "edited"}


def test_issue_comment_covers_created_edited_and_deleted_comments() -> None:
    assert _triggers()["issue_comment"]["types"] == ["created", "edited", "deleted"]
    assert "Stand-in oracle review: APPROVE" in _jobs()["gate"]["if"]


def test_workflow_run_bridges_both_posting_workflows() -> None:
    bridge = _triggers()["workflow_run"]
    assert bridge["types"] == ["completed"]
    assert set(bridge["workflows"]) == {"oracle-verdict", "oracle-review"}


def test_dispatch_takes_a_pr_number_and_schedule_sweeps_hourly() -> None:
    assert _triggers()["workflow_dispatch"]["inputs"]["pr"]["required"] is True
    assert len(_triggers()["schedule"]) == 1


def test_dispatch_head_sha_is_optional() -> None:
    head = _triggers()["workflow_dispatch"]["inputs"]["head_sha"]
    assert head["required"] is False
    assert head["type"] == "string"


def test_gate_passes_the_head_only_when_the_dispatch_input_is_valid() -> None:
    resolve = next(step for step in _steps("gate") if step["name"] == "Resolve the pull request")
    assert resolve["env"]["INPUT_HEAD"] == "${{ inputs.head_sha }}"
    assert "grep -Eq '^[0-9a-f]{40}$'" in resolve["run"]
    assert "head_sha=$HEAD_SHA" in resolve["run"]
    for name in ("Post the gate for the head", "Log the gate without posting when the App is not configured"):
        step = next(step for step in _steps("gate") if step["name"] == name)
        assert step["env"]["KNOWN_HEAD"] == "${{ steps.resolve.outputs.head_sha }}"
        assert 'if [ -n "$KNOWN_HEAD" ]; then HEAD_ARGS=(--head-sha "$KNOWN_HEAD"); fi' in step["run"]
        assert '"${HEAD_ARGS[@]}"' in step["run"]


def _checkout_violations(workflow: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            if "actions/checkout" not in step.get("uses", ""):
                continue
            with_section = step.get("with", {})
            if with_section.get("ref") != "develop" or with_section.get("persist-credentials") is not False:
                found.append(f"{name}: checkout is not the trusted develop copy")
            if "_project/scripts/oracle_gate.py" not in with_section.get("sparse-checkout", ""):
                found.append(f"{name}: checkout does not carry the gate script")
            for key, value in with_section.items():
                if "github.event.pull_request.head" in str(value) or "github.head_ref" in str(value):
                    found.append(f"{name}: checkout {key} references the pull request head")
    return found


def test_no_step_checks_out_pull_request_code() -> None:
    text = _text()
    assert "github.head_ref" not in text
    assert "pull/" not in text
    assert _checkout_violations(_load()) == []
    for name, job in _jobs().items():
        for step in job["steps"]:
            assert "pull_request.head" not in step.get("run", ""), name
    resolve = next(step for step in _steps("gate") if step["name"] == "Resolve the pull request")
    assert resolve["env"]["EVENT_HEAD"] == "${{ github.event.pull_request.head.sha }}"
    assert text.count("github.event.pull_request.head") == 1
    assert text.count("github.event.pull_request.head.sha") == 1


@pytest.mark.parametrize("key", ["ref", "repository", "token"])
def test_a_checkout_of_the_pull_request_head_is_rejected(key: str) -> None:
    workflow = _load()
    checkout = next(step for step in workflow["jobs"]["gate"]["steps"] if "actions/checkout" in step.get("uses", ""))
    checkout["with"][key] = "${{ github.event.pull_request.head.sha }}"
    assert any("references the pull request head" in violation for violation in _checkout_violations(workflow))


def test_a_checkout_through_head_ref_is_rejected() -> None:
    workflow = _load()
    checkout = next(step for step in workflow["jobs"]["sweep"]["steps"] if "actions/checkout" in step.get("uses", ""))
    checkout["with"]["ref"] = "${{ github.head_ref }}"
    assert any("references the pull request head" in violation for violation in _checkout_violations(workflow))


def test_pull_request_target_supplies_the_head_through_the_resolve_output() -> None:
    resolve = next(step for step in _steps("gate") if step["name"] == "Resolve the pull request")
    assert resolve["env"]["INPUT_HEAD"] == "${{ inputs.head_sha }}"
    assert resolve["env"]["EVENT_HEAD"] == "${{ github.event.pull_request.head.sha }}"
    run = resolve["run"]
    assert 'HEAD_SHA="${INPUT_HEAD:-$EVENT_HEAD}"' in run
    assert "grep -Eq '^[0-9a-f]{40}$'" in run
    assert run.index("grep -Eq") < run.index('echo "head_sha=$HEAD_SHA" >> "$GITHUB_OUTPUT"')
    assert "printf '%s' \"$HEAD_SHA\" | grep -Eq" in run


def test_sparse_checkout_stays_in_the_gate_and_its_predicate() -> None:
    for name in _jobs():
        for step in _steps(name):
            if "actions/checkout" in step.get("uses", ""):
                sparse = step["with"]["sparse-checkout"]
                assert "_project/scripts/oracle_review_check.py" in sparse
                assert "_project/scripts/soundness_paths.py" in sparse
                assert ".github/soundness-paths.txt" in sparse
                assert ".github/oracle-reviewers.yml" in sparse
                assert "_project/scripts/oracle_reviewers" in sparse
                assert "_project/scripts/inert_paths.py" in sparse
                assert ".github/inert-paths.txt" in sparse


def test_sparse_checkout_is_enough_to_import_the_gate(tmp_path: Path) -> None:
    repo = REPO_ROOT
    for name in _jobs():
        for step in _steps(name):
            if "actions/checkout" not in step.get("uses", ""):
                continue
            root = tmp_path / name
            for entry in step["with"]["sparse-checkout"].split():
                source = repo / entry
                target = root / entry
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
                else:
                    shutil.copy2(source, target)
            probe = "import sys; sys.path.insert(0, '_project/scripts'); import oracle_gate; oracle_gate.load_policy_rules()"
            result = subprocess.run([sys.executable, "-c", probe], cwd=root, capture_output=True, text=True)
            assert result.returncode == 0, f"{name}: {result.stderr}"


def _script_jobs() -> list[str]:
    return [name for name in _jobs() if any(SCRIPT in step.get("run", "") for step in _steps(name))]


def test_each_job_installs_uv_before_running_the_script() -> None:
    assert set(_script_jobs()) == {"gate", "sweep"}
    for name in _script_jobs():
        names = [step.get("name", "") for step in _steps(name)]
        script_at = next(index for index, step in enumerate(_steps(name)) if SCRIPT in step.get("run", ""))
        assert "Install uv" in names, name
        assert names.index("Install uv") < script_at, name


def test_gate_runs_the_script_with_pinned_pyyaml() -> None:
    for name in _jobs():
        for step in _steps(name):
            if SCRIPT in step.get("run", ""):
                assert "uv run --isolated --no-project --python 3.12 --with pyyaml==6.0.3 -- python" in step["run"], (
                    name,
                    step.get("name"),
                )


def test_every_gate_script_step_exports_the_posting_token() -> None:
    for name in _jobs():
        for step in _steps(name):
            if SCRIPT not in step.get("run", ""):
                continue
            env = step.get("env", {})
            assert "GITHUB_TOKEN" in env, (name, step.get("name"))
            assert "GH_TOKEN" in env, (name, step.get("name"))


def test_posting_steps_export_the_app_token() -> None:
    post = next(step for step in _steps("gate") if step["name"] == "Post the gate for the head")
    assert post["env"]["GITHUB_TOKEN"] == "${{ steps.app.outputs.token }}"
    log = next(step for step in _steps("gate") if step["name"].startswith("Log the gate without posting"))
    assert log["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    sweep = next(step for step in _steps("sweep") if step["name"].startswith("Gate every open"))
    assert sweep["env"]["GITHUB_TOKEN"] == "${{ steps.app.outputs.token || github.token }}"


def test_dispatch_is_refused_outside_develop() -> None:
    guard = next(step for step in _steps("gate") if step["name"].startswith("Refuse a dispatch"))
    assert guard["if"] == "github.event_name == 'workflow_dispatch'"
    assert "refs/heads/develop" in guard["run"]
    assert "exit 2" in guard["run"]


def test_completion_runs_dispatch_the_gate_for_the_develop_pull_request_at_that_head() -> None:
    bridge = _jobs()["bridge"]
    assert bridge["if"] == "github.event_name == 'workflow_run'"
    assert bridge["permissions"] == {"actions": "write", "contents": "read", "pull-requests": "read"}
    (step,) = bridge["steps"]
    assert "pull_requests" not in str(step["env"])
    assert step["env"]["HEAD_SHA"] == "${{ github.event.workflow_run.head_sha }}"
    assert "*[!0-9a-f]*" in step["run"]
    assert '.base.ref == "develop"' in step["run"]
    assert ".head.sha == $sha" in step["run"]
    assert 'gh workflow run oracle-gate.yml --repo "$REPO" --ref develop -f pr="$PR_NUMBER"' in step["run"]
    assert '-f head_sha="$HEAD_SHA"' in step["run"]
    assert SCRIPT not in step["run"]
    assert "github.event_name != 'workflow_run'" in _jobs()["gate"]["if"]
    resolve = next(step for step in _steps("gate") if step["name"] == "Resolve the pull request")
    assert "workflow_run" not in str(resolve)


def test_app_token_mint_matches_the_verdict_post_job() -> None:
    verdict_text = VERDICT.read_text(encoding="utf-8")
    assert "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1" in verdict_text
    for name in _script_jobs():
        mint = [step for step in _steps(name) if "create-github-app-token" in step.get("uses", "")]
        assert len(mint) == 1, name
        assert mint[0]["with"] == {
            "app-id": "${{ secrets.ORACLE_APP_ID }}",
            "private-key": "${{ secrets.ORACLE_APP_PRIVATE_KEY }}",
            "permission-statuses": "write",
            "permission-pull-requests": "write",
        }, name
        assert _jobs()[name]["environment"] == "oracle", name


def test_gate_runs_the_script_with_the_resolved_pr() -> None:
    post = next(step for step in _steps("gate") if step["name"] == "Post the gate for the head")
    assert SCRIPT in post["run"]
    assert '--pr "$PR_NUMBER"' in post["run"]
    assert post["env"]["PR_NUMBER"] == "${{ steps.resolve.outputs.pr }}"


def test_sweep_covers_every_open_develop_pull_request() -> None:
    sweep = next(step for step in _steps("sweep") if step["name"].startswith("Gate every open"))
    assert "gh api --paginate" in sweep["run"]
    assert "pulls?base=develop&state=open&per_page=100" in sweep["run"]
    assert "gh pr list" not in sweep["run"]
    assert "--limit" not in sweep["run"]
    assert SCRIPT in sweep["run"]
    assert "--jq '.[] | \"\\(.number) \\(.head.sha)\"'" in sweep["run"]
    assert "while read -r PR_NUMBER HEAD_SHA" in sweep["run"]
    assert '--head-sha "$HEAD_SHA"' in sweep["run"]
    assert "< /dev/null" in sweep["run"]


def test_sweep_timeout_covers_the_measured_per_pull_request_time() -> None:
    measured_seconds_per_pull_request = 5
    open_develop_pull_requests = 16
    timeout_seconds = _jobs()["sweep"]["timeout-minutes"] * 60
    assert timeout_seconds >= 30 * measured_seconds_per_pull_request * open_develop_pull_requests


def test_sweep_fails_visibly_when_a_pull_request_errors() -> None:
    sweep = next(step for step in _steps("sweep") if step["name"].startswith("Gate every open"))
    assert 'FAILED=""' in sweep["run"]
    assert "::warning::oracle-gate failed for" in sweep["run"]
    assert "::error::oracle-gate failed for" in sweep["run"]
    assert "exit 1" in sweep["run"]


def test_sweep_counts_a_pull_request_as_evaluated_only_when_the_gate_reports_its_exit() -> None:
    gate = _script_module(REPO_ROOT / SCRIPT, "oracle_gate_under_workflow_test")
    run = next(step for step in _steps("sweep") if step["name"].startswith("Gate every open"))["run"]
    assert run.index('rm -f "$RESULT"') < run.index(f'{gate.RESULT_FILE_ENV}="$RESULT" uv run')
    assert "|| STATUS=$?" in run
    assert '[ "$REPORTED" != "$STATUS" ]' in run
    assert f'[ "$STATUS" -ne {gate.PASS} ] && [ "$STATUS" -ne {gate.WAITING_EXIT} ]' in run
    assert gate.WAITING_EXIT not in (1, gate.ERROR)


def test_concurrency_is_one_group_per_pull_request() -> None:
    group = _load()["concurrency"]["group"]
    assert "github.event.pull_request.number" in group
    assert "github.event.issue.number" in group
    assert "inputs.pr" in group
    assert "pull_requests" not in group
    assert "github.run_id" in group
    assert "cancel-in-progress: true" not in _text()
    assert _load()["concurrency"]["cancel-in-progress"] == "${{ github.event_name != 'push' }}"


def test_completion_runs_group_by_head_sha_apart_from_pull_request_gates() -> None:
    group = _load()["concurrency"]["group"]
    assert "(github.event_name == 'workflow_run' && format('bridge-{0}', github.event.workflow_run.head_sha))" in group
    assert group.count("github.event.workflow_run.head_sha") == 1
    assert group.index("bridge-") < group.index("github.event.pull_request.number")
    assert group.index("bridge-") < group.rindex("github.run_id")


def _push_context() -> dict[str, Any]:
    return {"github": {"event_name": "push", "event": {}, "run_id": "55", "sha": SHA}, "inputs": {}}


def test_push_runs_only_the_sweep() -> None:
    context = _push_context()
    assert _evaluate(_jobs()["sweep"]["if"], context)
    assert not _evaluate(_jobs()["gate"]["if"], context)
    assert not _evaluate(_jobs()["bridge"]["if"], context)


def test_push_sweeps_share_a_group_apart_from_pull_request_gates() -> None:
    group = _render(_load()["concurrency"]["group"], _push_context())
    assert group == "oracle-gate-push-sweep"
    for number in (7, 8):
        event = {"pull_request": {"number": number}}
        context = {"github": {"event_name": "pull_request_target", "event": event, "run_id": "55"}, "inputs": {}}
        assert _render(_load()["concurrency"]["group"], context) == f"oracle-gate-{number}"
    cancel = _load()["concurrency"]["cancel-in-progress"].removeprefix("${{").removesuffix("}}")
    assert _evaluate(cancel, _push_context()) is False
    assert _evaluate(cancel, {"github": {"event_name": "pull_request_target"}}) is True


def test_workflow_permissions_are_empty_and_jobs_stay_minimal() -> None:
    assert _load()["permissions"] == {}
    allowed = {"contents", "pull-requests", "actions", "issues"}
    for name, job in _jobs().items():
        assert set(job.get("permissions", {})) <= allowed, name


def test_actions_are_pinned_to_known_shas() -> None:
    for name, job in _jobs().items():
        for step in job["steps"]:
            if "uses" in step:
                assert SHA_PIN.match(step["uses"]), (name, step["uses"])
                assert step["uses"].split("@")[1] in KNOWN_SHAS, (name, step["uses"])


def test_no_other_workflow_can_post_the_gate_context() -> None:
    variable_context = re.compile(r"context[\"']?\s*[=:]\s*[\"']?\$")
    offenders = []
    for workflow in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        if workflow.name == "oracle-gate.yml":
            continue
        data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job_name, job in (data.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                run = step.get("run", "")
                if "oracle-gate" in str(step) or ("statuses/" in run and variable_context.search(run)):
                    offenders.append((workflow.name, job_name, step.get("name")))
    assert offenders == []
    gate_py = (REPO_ROOT / "_project/scripts/oracle_gate.py").read_text(encoding="utf-8")
    assert 'GATE_CONTEXT = "oracle-gate"' in gate_py


def test_workflow_has_no_comments() -> None:
    assert not any(line.lstrip().startswith("#") for line in _text().splitlines())


SHA = "0123456789abcdef0123456789abcdef01234567"
MARKER = "Stand-in oracle review: APPROVE"
STANDIN = f"{MARKER} {SHA}"


def _comment_event(action: str, body: str | None, previous: str | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {"action": action, "issue": {"number": 8, "pull_request": {"url": "x"}}}
    event["comment"] = {"body": body}
    if previous is not None:
        event["changes"] = {"body": {"from": previous}}
    return event


def _gate_runs(event: dict[str, Any]) -> bool:
    return bool(_evaluate(_jobs()["gate"]["if"], {"github": {"event_name": "issue_comment", "event": event}}))


def _group(event: dict[str, Any]) -> str:
    context = {"github": {"event_name": "issue_comment", "event": event, "run_id": "55"}, "inputs": {}}
    return _render(_load()["concurrency"]["group"], context)


def test_created_marker_comment_runs_the_gate_in_the_pull_request_group() -> None:
    event = _comment_event("created", STANDIN)
    assert _gate_runs(event)
    assert _group(event) == "oracle-gate-8"


def test_edit_that_removes_the_marker_runs_the_gate_in_the_pull_request_group() -> None:
    event = _comment_event("edited", "withdrawn", STANDIN)
    assert _gate_runs(event)
    assert _group(event) == "oracle-gate-8"


def test_edit_that_adds_the_marker_runs_the_gate() -> None:
    event = _comment_event("edited", STANDIN, "draft")
    assert _gate_runs(event)
    assert _group(event) == "oracle-gate-8"


def test_deleted_marker_comment_runs_the_gate_in_the_pull_request_group() -> None:
    event = _comment_event("deleted", STANDIN)
    assert _gate_runs(event)
    assert _group(event) == "oracle-gate-8"


def test_plain_comment_edit_skips_the_gate_and_never_joins_the_pull_request_group() -> None:
    event = _comment_event("edited", "looks good", "looks fine")
    assert not _gate_runs(event)
    assert _group(event) == "oracle-gate-ignored-55"


def test_plain_comment_deletion_skips_the_gate_and_never_joins_the_pull_request_group() -> None:
    event = _comment_event("deleted", "looks good")
    assert not _gate_runs(event)
    assert _group(event) == "oracle-gate-ignored-55"


def test_edit_without_a_body_change_skips_the_gate() -> None:
    event = _comment_event("edited", "looks good")
    assert not _gate_runs(event)
    assert _group(event) == "oracle-gate-ignored-55"


def test_previous_body_is_ignored_for_events_that_are_not_edits() -> None:
    event = _comment_event("created", "plain", STANDIN)
    assert not _gate_runs(event)


def test_comment_on_a_plain_issue_never_runs_the_gate() -> None:
    event = _comment_event("created", STANDIN)
    del event["issue"]["pull_request"]
    assert not _gate_runs(event)


@pytest.mark.parametrize("padding", [" ", "\n", "\t", "\r\n", "  \n\n"])
def test_trigger_agrees_with_decide_on_leading_whitespace(padding: str) -> None:
    body = f"{padding}{STANDIN}"
    assert _attested_shas({"body": body}) == [SHA]
    assert _gate_runs(_comment_event("created", body))
    assert _gate_runs(_comment_event("edited", "withdrawn", body))
    assert _gate_runs(_comment_event("deleted", body))
    assert _group(_comment_event("created", body)) == "oracle-gate-8"


@pytest.mark.parametrize("trailing", [" ", "\n", "\r\n"])
def test_trigger_agrees_with_decide_on_trailing_whitespace(trailing: str) -> None:
    body = f"{STANDIN}{trailing}"
    assert _attested_shas({"body": body}) == [SHA]
    assert _gate_runs(_comment_event("created", body))


def test_non_comment_events_keep_the_gate_conditions() -> None:
    for name in ("pull_request_target", "workflow_dispatch"):
        assert _evaluate(_jobs()["gate"]["if"], {"github": {"event_name": name, "event": {}}})
    for name in ("schedule", "workflow_run", "push"):
        assert not _evaluate(_jobs()["gate"]["if"], {"github": {"event_name": name, "event": {}}})


def _run_name(event_name: str, event: dict[str, Any], inputs: dict[str, Any] | None = None) -> str:
    context = {"github": {"event_name": event_name, "event": event}, "inputs": inputs or {}}
    return _render(_load()["run-name"], context)


def _render(template: str, context: dict[str, Any]) -> str:
    return re.sub(r"\$\{\{(.*?)\}\}(?=[ )\"]|$)", lambda m: str(_evaluate(m.group(1), context) or ""), template)


def _evaluate(expression: str, context: dict[str, Any]) -> Any:
    expression = " ".join(expression.split())

    def value(expression: str) -> Any:
        expression = expression.strip()
        if expression.startswith("(") and _closing_paren(expression) == len(expression) - 1:
            return value(expression[1:-1])
        for operator in ("||", "&&"):
            parts = _split_top_level(expression, operator)
            if len(parts) > 1:
                result = value(parts[0])
                for part in parts[1:]:
                    if operator == "||":
                        result = result or value(part)
                    else:
                        result = value(part) if result else result
                return result
        for operator in ("==", "!="):
            parts = _split_top_level(expression, operator)
            if len(parts) == 2:
                return (value(parts[0]) == value(parts[1])) == (operator == "==")
        if expression.startswith("!"):
            return not value(expression[1:])
        if expression.startswith("'"):
            return expression[1:-1]
        for name, test in (("contains(", lambda a, b: b in a), ("startsWith(", lambda a, b: a.startswith(b))):
            if expression.startswith(name):
                left, right = _split_top_level(expression[len(name) : -1], ",")
                return test(str(value(left) or ""), str(value(right) or ""))
        if expression.startswith("format("):
            template, *arguments = _split_top_level(expression[len("format(") : -1], ",")
            return re.sub(r"\{(\d+)\}", lambda m: str(value(arguments[int(m.group(1))])), value(template))
        found: Any = context
        for key in expression.split("."):
            found = found.get(key) if isinstance(found, dict) else None
        return found

    return value(expression)


def _closing_paren(expression: str) -> int:
    depth = 0
    for index, char in enumerate(expression):
        depth += char == "("
        depth -= char == ")"
        if depth == 0:
            return index
    return -1


def _split_top_level(expression: str, separator: str) -> list[str]:
    parts, depth, quoted, start = [], 0, False, 0
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "'":
            quoted = not quoted
        elif not quoted and char == "(":
            depth += 1
        elif not quoted and char == ")":
            depth -= 1
        elif not quoted and depth == 0 and expression.startswith(separator, index):
            parts.append(expression[start:index])
            start = index + len(separator)
            index = start - 1
        index += 1
    parts.append(expression[start:])
    return parts


def test_only_the_scheduled_and_push_runs_are_titled_sweep() -> None:
    assert _run_name("schedule", {}) == "oracle-gate (schedule sweep)"
    assert _run_name("push", {}) == "oracle-gate (push sweep)"
    titles = {
        "workflow_run": _run_name("workflow_run", {"workflow_run": {"head_sha": SHA}}),
        "pull_request_target": _run_name("pull_request_target", {"pull_request": {"number": 7}}),
        "issue_comment": _run_name("issue_comment", {"issue": {"number": 8}}),
        "workflow_dispatch": _run_name("workflow_dispatch", {}, {"pr": "9"}),
    }
    assert all("sweep" not in title for title in titles.values())


def test_workflow_run_title_carries_the_source_head_sha() -> None:
    assert _run_name("workflow_run", {"workflow_run": {"head_sha": SHA}}) == f"oracle-gate (workflow_run sha={SHA})"


def test_pull_request_comment_and_dispatch_titles_keep_the_pr_number() -> None:
    assert _run_name("pull_request_target", {"pull_request": {"number": 7}}) == "oracle-gate (pull_request_target pr=7)"
    assert _run_name("issue_comment", {"issue": {"number": 8}}) == "oracle-gate (issue_comment pr=8)"
    assert _run_name("workflow_dispatch", {}, {"pr": "9"}) == "oracle-gate (workflow_dispatch pr=9)"
