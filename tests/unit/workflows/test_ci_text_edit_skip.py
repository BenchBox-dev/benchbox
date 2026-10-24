from __future__ import annotations

import ast
import copy
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ci_text_edit_verdict import TEXT_EDIT_RUN_NAME

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
GATE_SCRIPT = ROOT / "_project/scripts/oracle_gate.py"
ADMIN_SETTINGS = ROOT / "docs/internal/repo-admin-settings.md"
TEXT_EDIT = "github.event.action == 'edited' && !github.event.changes.base"
ROOT_JOBS = ("comment-policy", "ci-paths")
VERDICT_JOB = "text-edit-verdict"
RUN_ID = 900
PR_NUMBER = 42


TOKEN = re.compile(r"\s*(\|\||&&|==|!=|!|\(|\)|,|'[^']*'|[0-9]+|[A-Za-z_][A-Za-z0-9_.-]*)")
FUNCTIONS = {"always": True, "cancelled": False, "success": True, "failure": False}


def _tokens(body: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    while body[position:].strip():
        match = TOKEN.match(body, position)
        assert match, f"cannot parse expression at {body[position:]!r}"
        tokens.append(match.group(1))
        position = match.end()
    return tokens


def _truthy(value: Any) -> bool:
    return value not in (None, False, "", 0)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


class _Parser:
    def __init__(self, body: str, context: dict[str, Any]) -> None:
        self.tokens = _tokens(body)
        self.context = context

    def _take(self, expected: str | None = None) -> str:
        token = self.tokens.pop(0)
        assert expected is None or token == expected, f"expected {expected!r}, found {token!r}"
        return token

    def _peek(self) -> str | None:
        return self.tokens[0] if self.tokens else None

    def parse(self) -> Any:
        value = self._or()
        assert not self.tokens, f"unparsed tokens {self.tokens}"
        return value

    def _or(self) -> Any:
        value = self._and()
        while self._peek() == "||":
            self._take()
            right = self._and()
            value = value if _truthy(value) else right
        return value

    def _and(self) -> Any:
        value = self._compare()
        while self._peek() == "&&":
            self._take()
            right = self._compare()
            value = right if _truthy(value) else value
        return value

    def _compare(self) -> Any:
        value = self._unary()
        if self._peek() in ("==", "!="):
            operator = self._take()
            right = self._unary()
            same = _text(value).casefold() == _text(right).casefold() and (value is None) == (right is None)
            return same if operator == "==" else not same
        return value

    def _unary(self) -> Any:
        if self._peek() == "!":
            self._take()
            return not _truthy(self._unary())
        return self._primary()

    def _primary(self) -> Any:
        token = self._take()
        if token == "(":
            value = self._or()
            self._take(")")
            return value
        if token.startswith("'"):
            return token[1:-1]
        if token.isdigit():
            return int(token)
        if token in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[token]
        if self._peek() == "(":
            self._take()
            arguments: list[Any] = []
            while self._peek() != ")":
                arguments.append(self._or())
                if self._peek() == ",":
                    self._take()
            self._take(")")
            if token == "format":
                return re.sub(r"\{(\d+)\}", lambda match: _text(arguments[int(match.group(1)) + 1]), arguments[0])
            return FUNCTIONS[token]
        value: Any = self.context
        for part in token.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return value


def _evaluate_body(body: str, event: dict[str, Any]) -> Any:
    context = {"github": {"event": event, "run_id": RUN_ID, "ref": "refs/pull/42/merge"}}
    return _Parser(body, context).parse()


def evaluate(expression: str, event: dict[str, Any]) -> Any:
    text = expression.strip()
    whole = re.fullmatch(r"\$\{\{(.*)\}\}", text, re.DOTALL)
    if whole and "${{" not in whole.group(1):
        return _evaluate_body(whole.group(1), event)
    if "${{" in text:
        return re.sub(r"\$\{\{(.*?)\}\}", lambda match: str(_evaluate_body(match.group(1), event)), text)
    return _evaluate_body(text, event)


def _event(action: str, changes: dict[str, Any] | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {"action": action, "pull_request": {"number": PR_NUMBER}}
    if changes is not None:
        event["changes"] = changes
    return event


TITLE = {"title": {"from": "old"}}
BODY = {"body": {"from": "old"}}
BASE = {"base": {"ref": {"from": "parent"}, "sha": {"from": "a" * 40}}}
TEXT_EVENTS = {
    "title": _event("edited", TITLE),
    "body": _event("edited", BODY),
    "title-and-body": _event("edited", {**TITLE, **BODY}),
}
OTHER_EVENTS = {
    "base": _event("edited", BASE),
    "base-and-text": _event("edited", {**BASE, **TITLE, **BODY}),
    "opened": _event("opened"),
    "synchronize": _event("synchronize"),
    "reopened": _event("reopened"),
    "ready_for_review": _event("ready_for_review"),
}


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _gate_context() -> str:
    if not GATE_SCRIPT.exists():
        pytest.skip("_project scripts are not in this checkout (release tree)")
    tree = ast.parse(GATE_SCRIPT.read_text(encoding="utf-8"))
    return next(
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "GATE_CONTEXT" for target in node.targets)
        and isinstance(node.value, ast.Constant)
    )


def _required_names() -> set[str]:
    text = ADMIN_SETTINGS.read_text(encoding="utf-8")
    block = text.split("Required status checks:", 1)[1].split("```text", 1)[1].split("```", 1)[0]
    required = {line.removeprefix("- ").strip() for line in block.splitlines() if line.strip()}
    elsewhere = {
        str(job.get("name", job_id))
        for path in WORKFLOW.parent.glob("*.yml")
        if path != WORKFLOW
        for job_id, job in (yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs") or {}).items()
    } | {_gate_context()}
    return required - elsewhere


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _reaches_paths(jobs: dict[str, Any], job_id: str) -> bool:
    return any(need == "ci-paths" or _reaches_paths(jobs, need) for need in _needs(jobs[job_id]))


def _runs_after_skips(job: dict[str, Any]) -> bool:
    condition = str(job.get("if", ""))
    return "always()" in condition or "cancelled()" in condition


def name_problems(workflow: dict[str, Any], required: set[str]) -> list[str]:
    problems: list[str] = []
    published: set[str] = set()
    for job_id, job in workflow["jobs"].items():
        if job_id == VERDICT_JOB:
            continue
        name = str(job.get("name", job_id))
        for label, event in {**TEXT_EVENTS, **OTHER_EVENTS}.items():
            value = str(evaluate(name, event)) if "github.event" in name else name
            if label in TEXT_EVENTS and value in required:
                problems.append(f"{job_id} publishes required name {value!r} on a {label} edit")
            if label not in TEXT_EVENTS:
                published.add(value)
    problems += [f"no job publishes required name {name!r}" for name in sorted(required - published)]
    verdict = workflow["jobs"].get(VERDICT_JOB)
    if verdict is None:
        return [*problems, f"no {VERDICT_JOB} job reports the required names on a text edit"]
    name = str(verdict.get("name", VERDICT_JOB))
    if name != "${{ matrix.unit }}":
        problems.append(f"{VERDICT_JOB} is not named after its matrix unit")
    if skipped_check_name(name) in required:
        problems.append(f"{VERDICT_JOB} publishes required name {skipped_check_name(name)!r} when it is skipped")
    units = set(verdict.get("strategy", {}).get("matrix", {}).get("unit", []))
    problems += [f"a text edit reports no {name!r} check" for name in sorted(required - units)]
    problems += [f"a text edit reports {name!r}, which is not a required check" for name in sorted(units - required)]
    return problems


def skipped_check_name(name: str) -> str:
    text = name.strip()
    if text.startswith("${{") and text.endswith("}}"):
        return text[3:-2].strip()
    return text


def guard_problems(workflow: dict[str, Any]) -> list[str]:
    jobs = workflow["jobs"]
    problems: list[str] = []
    for job_id, job in jobs.items():
        if job_id == VERDICT_JOB:
            condition = str(job.get("if", "${{ true }}"))
            problems += [
                f"{job_id} skips a {label} edit"
                for label, event in TEXT_EVENTS.items()
                if not evaluate(condition, event)
            ]
            problems += [
                f"{job_id} runs on {label}" for label, event in OTHER_EVENTS.items() if evaluate(condition, event)
            ]
            if _needs(job):
                problems.append(f"{job_id} waits on jobs that a text edit skips")
            continue
        guarded = job_id in ROOT_JOBS or _runs_after_skips(job)
        if guarded:
            for label, event in TEXT_EVENTS.items():
                if evaluate(str(job.get("if", "${{ true }}")), event):
                    problems.append(f"{job_id} runs on a {label} edit")
            for label, event in OTHER_EVENTS.items():
                if job_id in ROOT_JOBS and not evaluate(str(job.get("if", "${{ true }}")), event):
                    problems.append(f"{job_id} does not run on {label}")
        elif not _reaches_paths(jobs, job_id):
            problems.append(f"{job_id} neither carries the text-edit guard nor needs ci-paths")
    return problems


def run_name_problems(workflow: dict[str, Any]) -> list[str]:
    run_name = str(workflow.get("run-name", ""))
    problems = [
        f"a {label} edit run is not named {TEXT_EDIT_RUN_NAME!r}"
        for label, event in TEXT_EVENTS.items()
        if evaluate(run_name, event) != TEXT_EDIT_RUN_NAME
    ]
    problems += [
        f"a {label} run is named like a text edit"
        for label, event in OTHER_EVENTS.items()
        if evaluate(run_name, event) == TEXT_EDIT_RUN_NAME
    ]
    return problems


def concurrency_problems(workflow: dict[str, Any]) -> list[str]:
    group = workflow["concurrency"]["group"]
    problems: list[str] = []
    for label, event in TEXT_EVENTS.items():
        if evaluate(group, event) != f"ci-text-{RUN_ID}":
            problems.append(f"a {label} edit does not get its own concurrency group")
    for label, event in OTHER_EVENTS.items():
        if evaluate(group, event) != f"ci-{PR_NUMBER}":
            problems.append(f"{label} does not use the PR's concurrency group")
    if workflow["concurrency"].get("cancel-in-progress") is not True:
        problems.append("cancel-in-progress is not true")
    return problems


def test_only_text_edits_count_as_text_edits() -> None:
    assert all(evaluate(TEXT_EDIT, event) for event in TEXT_EVENTS.values())
    assert not any(evaluate(TEXT_EDIT, event) for event in OTHER_EVENTS.values())


def test_text_edit_runs_publish_required_names_only_from_the_verdict_job() -> None:
    assert name_problems(_workflow(), _required_names()) == []


def test_text_edit_runs_execute_only_the_verdict_job() -> None:
    assert guard_problems(_workflow()) == []


def test_text_edit_runs_are_named_so_the_verdict_job_ignores_them() -> None:
    assert run_name_problems(_workflow()) == []


def test_text_edit_runs_cancel_nothing() -> None:
    assert concurrency_problems(_workflow()) == []


def test_edited_stays_a_trigger_so_retargets_rerun_ci() -> None:
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))
    assert "edited" in triggers["pull_request"]["types"]


def _mutated(change: Any) -> dict[str, Any]:
    workflow = copy.deepcopy(_workflow())
    change(workflow)
    return workflow


def _text_name_is_core(workflow: dict[str, Any]) -> None:
    workflow["jobs"]["core"]["name"] = "core"


def _base_test_removed(workflow: dict[str, Any]) -> None:
    for job in workflow["jobs"].values():
        if "if" in job:
            job["if"] = str(job["if"]).replace(" && !github.event.changes.base", "")


def _descendant_always(workflow: dict[str, Any]) -> None:
    workflow["jobs"]["code-test"]["if"] = "${{ always() }}"


def _root_unguarded(workflow: dict[str, Any]) -> None:
    del workflow["jobs"]["ci-paths"]["if"]


def _shared_group(workflow: dict[str, Any]) -> None:
    workflow["concurrency"]["group"] = "ci-${{ github.event.pull_request.number || github.ref }}"


def _verdict_on_every_event(workflow: dict[str, Any]) -> None:
    workflow["jobs"][VERDICT_JOB]["if"] = "${{ true }}"


def _verdict_waits_on_ci_paths(workflow: dict[str, Any]) -> None:
    workflow["jobs"][VERDICT_JOB]["needs"] = ["ci-paths"]


def _verdict_drops_a_unit(workflow: dict[str, Any]) -> None:
    workflow["jobs"][VERDICT_JOB]["strategy"]["matrix"]["unit"].remove("docs")


def _verdict_removed(workflow: dict[str, Any]) -> None:
    del workflow["jobs"][VERDICT_JOB]


def _run_name_unmarked(workflow: dict[str, Any]) -> None:
    workflow["run-name"] = "CI"


def _verdict_named_core(workflow: dict[str, Any]) -> None:
    workflow["jobs"][VERDICT_JOB]["name"] = "core"


@pytest.mark.parametrize(
    ("change", "check"),
    [
        (_text_name_is_core, "names"),
        (_base_test_removed, "guards"),
        (_descendant_always, "guards"),
        (_root_unguarded, "guards"),
        (_shared_group, "concurrency"),
        (_verdict_on_every_event, "guards"),
        (_verdict_waits_on_ci_paths, "guards"),
        (_verdict_drops_a_unit, "names"),
        (_verdict_removed, "names"),
        (_run_name_unmarked, "run-name"),
        (_verdict_named_core, "names"),
    ],
)
def test_checks_catch_regressions(change: Any, check: str) -> None:
    workflow = _mutated(change)
    if check == "names":
        assert name_problems(workflow, _required_names())
    elif check == "guards":
        assert guard_problems(workflow)
    elif check == "run-name":
        assert run_name_problems(workflow)
    else:
        assert concurrency_problems(workflow)


def test_a_skipped_job_publishes_its_name_expression_without_the_delimiters() -> None:
    assert skipped_check_name("${{ matrix.unit }}") == "matrix.unit"
    assert skipped_check_name("core") == "core"
