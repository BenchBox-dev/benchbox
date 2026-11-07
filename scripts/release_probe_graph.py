from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sys
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release-v2.yml"
STATUS_FUNCTIONS = frozenset({"always", "cancelled", "failure", "success"})
PUBLISHING_PERMISSIONS = (("id-token", "write"), ("attestations", "write"), ("contents", "write"))
PUBLISHING_ACTIONS = ("pypa/gh-action-pypi-publish", "actions/attest-build-provenance", "actions/attest")
GATE_JOBS = ("admit", "install-matrix", "release-uat")
REAL_PUBLISH_JOB = "publish"
PROBE_PUBLISH_JOB = "publish-probe"
VARIANTS = ("reuse", "fallback")
TARGETS = ("pypi", "test-pypi")
RESULTS = ("success", "failure", "skipped")

_TOKEN = re.compile(
    r"""\s*(?:
    (?P<string>'(?:[^']|'')*')
    |(?P<number>-?\d+(?:\.\d+)?)
    |(?P<name>[A-Za-z_][A-Za-z0-9_-]*)
    |(?P<op>&&|\|\||==|!=|<=|>=|<|>|!|\(|\)|,|\.|\[|\]|\*)
    )""",
    re.VERBOSE,
)
_WRAPPED = re.compile(r"\s*\$\{\{(.*)\}\}\s*", re.DOTALL)


class ExpressionError(ValueError):
    pass


def _tokens(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    stripped = text.rstrip()
    while position < len(stripped):
        match = _TOKEN.match(stripped, position)
        if match is None or match.end() == position:
            raise ExpressionError(f"cannot tokenize {text!r} at offset {position}")
        kind = match.lastgroup or ""
        tokens.append((kind, match.group(kind)))
        position = match.end()
    return tokens


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = _tokens(text)
        self.index = 0

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise ExpressionError(f"unexpected end of expression {self.text!r}")
        self.index += 1
        return token

    def _accept(self, value: str) -> bool:
        token = self._peek()
        if token is not None and token[0] == "op" and token[1] == value:
            self.index += 1
            return True
        return False

    def _expect(self, value: str) -> None:
        if not self._accept(value):
            raise ExpressionError(f"expected {value!r} in {self.text!r}")

    def parse(self) -> tuple:
        node = self._or()
        if self._peek() is not None:
            raise ExpressionError(f"trailing tokens in {self.text!r}")
        return node

    def _or(self) -> tuple:
        node = self._and()
        while self._accept("||"):
            node = ("or", node, self._and())
        return node

    def _and(self) -> tuple:
        node = self._equality()
        while self._accept("&&"):
            node = ("and", node, self._equality())
        return node

    def _equality(self) -> tuple:
        node = self._comparison()
        while True:
            if self._accept("=="):
                node = ("eq", node, self._comparison())
            elif self._accept("!="):
                node = ("ne", node, self._comparison())
            else:
                return node

    def _comparison(self) -> tuple:
        node = self._unary()
        while True:
            for operator in ("<=", ">=", "<", ">"):
                if self._accept(operator):
                    node = ("cmp", operator, node, self._unary())
                    break
            else:
                return node

    def _unary(self) -> tuple:
        if self._accept("!"):
            return ("not", self._unary())
        return self._postfix(self._primary())

    def _primary(self) -> tuple:
        kind, value = self._take()
        if kind == "string":
            return ("literal", value[1:-1].replace("''", "'"))
        if kind == "number":
            return ("literal", float(value) if "." in value else int(value))
        if kind == "op" and value == "(":
            node = self._or()
            self._expect(")")
            return node
        if kind == "name":
            if value == "true":
                return ("literal", True)
            if value == "false":
                return ("literal", False)
            if value == "null":
                return ("literal", None)
            if self._accept("("):
                arguments: list[tuple] = []
                if not self._accept(")"):
                    arguments.append(self._or())
                    while self._accept(","):
                        arguments.append(self._or())
                    self._expect(")")
                return ("call", value.lower(), tuple(arguments))
            return ("name", value)
        raise ExpressionError(f"unexpected {value!r} in {self.text!r}")

    def _postfix(self, node: tuple) -> tuple:
        while True:
            if self._accept("."):
                kind, value = self._take()
                if kind not in {"name", "number"}:
                    raise ExpressionError(f"bad property access in {self.text!r}")
                node = ("member", node, ("literal", value))
            elif self._accept("["):
                key = self._or()
                self._expect("]")
                node = ("member", node, key)
            else:
                return node


def strip_expression(text: str) -> str:
    wrapped = _WRAPPED.fullmatch(text)
    return wrapped.group(1).strip() if wrapped else text.strip()


@cache
def parse(text: str) -> tuple:
    return _Parser(strip_expression(text)).parse()


def _walk(node: Any):
    if isinstance(node, tuple):
        yield node
        for child in node:
            yield from _walk(child)


def uses_status_function(text: str) -> bool:
    return any(node[0] == "call" and node[1] in STATUS_FUNCTIONS for node in _walk(parse(text)))


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(value)


def _number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if not value.strip():
            return 0.0
        try:
            return float(value)
        except ValueError:
            return math.nan
    return math.nan


def _loosely_equal(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return left is right
    if left is None and right is None:
        return True
    return _number(left) == _number(right)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _call(name: str, arguments: list[Any], status: dict[str, bool]) -> Any:
    if name in status:
        return status[name]
    if name == "always":
        return True
    if name == "fromjson":
        return json.loads(_text(arguments[0]))
    if name == "tojson":
        return json.dumps(arguments[0], indent=2)
    if name == "contains":
        haystack, needle = arguments
        if isinstance(haystack, list):
            return any(_loosely_equal(item, needle) for item in haystack)
        return _text(needle).casefold() in _text(haystack).casefold()
    if name == "startswith":
        return _text(arguments[0]).casefold().startswith(_text(arguments[1]).casefold())
    if name == "endswith":
        return _text(arguments[0]).casefold().endswith(_text(arguments[1]).casefold())
    if name == "format":
        template, *rest = arguments
        return re.sub(
            r"\{\{|\}\}|\{(\d+)\}",
            lambda m: {"{{": "{", "}}": "}"}.get(m.group(0)) or _text(rest[int(m.group(1))]),
            _text(template),
        )
    if name == "join":
        values, separator = (arguments + [","])[:2]
        return _text(separator).join(_text(item) for item in values) if isinstance(values, list) else _text(values)
    raise ExpressionError(f"unsupported function {name}()")


def _member(container: Any, key: Any) -> Any:
    if isinstance(container, dict):
        if key in container:
            return container[key]
        lowered = _text(key).casefold()
        return next((value for name, value in container.items() if str(name).casefold() == lowered), None)
    if isinstance(container, list) and isinstance(key, (int, float)):
        index = int(key)
        return container[index] if 0 <= index < len(container) else None
    return None


def _evaluate(node: tuple, context: dict[str, Any], status: dict[str, bool]) -> Any:
    kind = node[0]
    if kind == "literal":
        return node[1]
    if kind == "name":
        return _member(context, node[1])
    if kind == "member":
        return _member(_evaluate(node[1], context, status), _evaluate(node[2], context, status))
    if kind == "not":
        return not _truthy(_evaluate(node[1], context, status))
    if kind == "and":
        left = _evaluate(node[1], context, status)
        return _evaluate(node[2], context, status) if _truthy(left) else left
    if kind == "or":
        left = _evaluate(node[1], context, status)
        return left if _truthy(left) else _evaluate(node[2], context, status)
    if kind in {"eq", "ne"}:
        equal = _loosely_equal(_evaluate(node[1], context, status), _evaluate(node[2], context, status))
        return equal if kind == "eq" else not equal
    if kind == "cmp":
        left, right = _number(_evaluate(node[2], context, status)), _number(_evaluate(node[3], context, status))
        return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[node[1]]
    if kind == "call":
        return _call(node[1], [_evaluate(argument, context, status) for argument in node[2]], status)
    raise ExpressionError(f"unsupported expression node {kind}")


def evaluate(text: str, context: dict[str, Any], status: dict[str, bool] | None = None) -> Any:
    flags = status or {"success": True, "failure": False, "cancelled": False}
    return _evaluate(parse(text), context, flags)


def evaluate_template(text: str, context: dict[str, Any]) -> str:
    return re.sub(r"\$\{\{(.*?)\}\}", lambda m: _text(evaluate(m.group(1), context)), text, flags=re.DOTALL)


@dataclass(frozen=True)
class Scenario:
    event: str
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, dict[str, str]] = field(default_factory=dict)
    outcomes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class JobRun:
    result: str
    cells: int


def needs_of(job: dict[str, Any]) -> tuple[str, ...]:
    needs = job.get("needs", ())
    return (needs,) if isinstance(needs, str) else tuple(needs)


def ancestors(jobs: dict[str, Any]) -> dict[str, frozenset[str]]:
    resolved: dict[str, frozenset[str]] = {}

    def walk(name: str) -> frozenset[str]:
        if name not in resolved:
            direct = needs_of(jobs[name])
            resolved[name] = frozenset(direct).union(*(walk(parent) for parent in direct))
        return resolved[name]

    for name in jobs:
        walk(name)
    return resolved


def topological_order(jobs: dict[str, Any]) -> list[str]:
    order: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        for parent in needs_of(jobs[name]):
            visit(parent)
        order.append(name)

    for name in jobs:
        visit(name)
    return order


def matrix_cells(job: dict[str, Any], context: dict[str, Any]) -> int:
    matrix = (job.get("strategy") or {}).get("matrix")
    if matrix is None:
        return 1
    if isinstance(matrix, str):
        matrix = evaluate(matrix, context)
    if not isinstance(matrix, dict):
        raise ExpressionError(f"matrix is not an object: {matrix!r}")
    axes = {key: values for key, values in matrix.items() if key not in {"include", "exclude"}}
    combinations = [dict(zip(axes, values)) for values in itertools.product(*axes.values())] if axes else []
    excluded = matrix.get("exclude", [])
    combinations = [
        combination
        for combination in combinations
        if not any(all(combination.get(key) == value for key, value in drop.items()) for drop in excluded)
    ]
    for entry in matrix.get("include", []):
        pinned = {key: value for key, value in entry.items() if key in axes}
        if axes and (
            not pinned or any(all(c.get(key) == value for key, value in pinned.items()) for c in combinations)
        ):
            continue
        combinations.append(dict(entry))
    return max(len(combinations), 1)


def simulate(workflow: dict[str, Any], scenario: Scenario) -> dict[str, JobRun]:
    jobs = workflow["jobs"]
    family = ancestors(jobs)
    github = {"event_name": scenario.event}
    runs: dict[str, JobRun] = {}
    for name in topological_order(jobs):
        job = jobs[name]
        direct = needs_of(job)
        needs_context = {
            parent: {
                "result": runs[parent].result,
                "outputs": scenario.outputs.get(parent, {}) if runs[parent].result != "skipped" else {},
            }
            for parent in direct
        }
        context = {"github": github, "inputs": scenario.inputs, "needs": needs_context, "vars": {}}
        results = [runs[parent].result for parent in family[name]]
        status = {
            "success": all(result == "success" for result in results),
            "failure": any(result == "failure" for result in results),
            "cancelled": False,
        }
        condition = job.get("if")
        if condition is None:
            ran = status["success"]
        elif uses_status_function(str(condition)):
            ran = _truthy(evaluate(str(condition), context, status))
        else:
            ran = status["success"] and _truthy(evaluate(str(condition), context, status))
        if not ran:
            runs[name] = JobRun("skipped", 0)
            continue
        runs[name] = JobRun(scenario.outcomes.get(name, "success"), matrix_cells(job, context))
    return runs


def peak_runners(workflow: dict[str, Any], runs: dict[str, JobRun]) -> int:
    family = ancestors(workflow["jobs"])
    active = [name for name, run in runs.items() if run.result != "skipped"]

    def related(first: str, second: str) -> bool:
        return first in family[second] or second in family[first]

    best = 0

    def extend(chosen: list[str], rest: list[str], total: int) -> None:
        nonlocal best
        best = max(best, total)
        for index, name in enumerate(rest):
            if all(not related(name, other) for other in chosen):
                extend([*chosen, name], rest[index + 1 :], total + runs[name].cells)

    extend([], active, 0)
    return best


def job_can_publish(job: dict[str, Any]) -> bool:
    permissions = job.get("permissions")
    if isinstance(permissions, dict):
        for scope, level in PUBLISHING_PERMISSIONS:
            if permissions.get(scope) == level:
                return True
    if permissions == "write-all":
        return True
    if "environment" in job:
        return True
    return any(
        str(step.get("uses", "")).startswith(PUBLISHING_ACTIONS)
        or re.search(r"\b(twine\s+upload|gh\s+release\s+(create|upload|edit)|git\s+push)\b", str(step.get("run", "")))
        for step in job.get("steps", [])
    )


def publishing_jobs(workflow: dict[str, Any]) -> list[str]:
    return [name for name, job in workflow["jobs"].items() if job_can_publish(job)]


def load_workflow(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _varying(workflow: dict[str, Any]) -> list[str]:
    fixed = {*GATE_JOBS, "uat-reuse", "uat-stage1", "uat-stage2", "uat-stage3"}
    return [name for name in workflow["jobs"] if name in fixed or name.startswith("probe-")]


def scenarios(workflow: dict[str, Any], event: str, inputs: dict[str, Any]):
    varying = _varying(workflow)
    for reuse, target in itertools.product(("true", "false"), TARGETS):
        outputs = {"uat-reuse": {"reuse": reuse}, "admit": {"target": target}}
        for results in itertools.product(("success", "failure"), repeat=len(varying)):
            yield Scenario(event, inputs, outputs, dict(zip(varying, results)))


def _gates_green(runs: dict[str, JobRun]) -> bool:
    return all(runs[name].result == "success" for name in GATE_JOBS)


def probe_violations(workflow: dict[str, Any]) -> list[str]:
    capable = set(publishing_jobs(workflow)) - {PROBE_PUBLISH_JOB}
    offenders: set[str] = set()
    for variant in VARIANTS:
        for scenario in scenarios(workflow, "workflow_dispatch", {"probe": variant}):
            runs = simulate(workflow, scenario)
            offenders |= {name for name in capable if runs[name].result != "skipped"}
    return [f"{name} can run in probe mode although it can publish" for name in sorted(offenders)]


def real_violations(workflow: dict[str, Any]) -> list[str]:
    found: dict[str, None] = {}
    for event, inputs in (("push", {}), ("workflow_dispatch", {"probe": "none"})):
        for scenario in scenarios(workflow, event, inputs):
            runs = simulate(workflow, scenario)
            green = _gates_green(runs)
            reuse = scenario.outputs["uat-reuse"]["reuse"]
            if green and runs[REAL_PUBLISH_JOB].result == "skipped":
                found[f"{REAL_PUBLISH_JOB} is skipped after green {', '.join(GATE_JOBS)} ({event}, reuse={reuse})"] = (
                    None
                )
            if not green and runs[REAL_PUBLISH_JOB].result != "skipped":
                found[f"{REAL_PUBLISH_JOB} runs although a gate did not succeed ({event}, reuse={reuse})"] = None
            if runs.get(PROBE_PUBLISH_JOB, JobRun("skipped", 0)).result != "skipped":
                found[f"{PROBE_PUBLISH_JOB} runs outside probe mode ({event})"] = None
    return list(found)


def probe_publish_violations(workflow: dict[str, Any]) -> list[str]:
    if PROBE_PUBLISH_JOB not in workflow["jobs"]:
        return [f"the workflow has no {PROBE_PUBLISH_JOB} job"]
    found: dict[str, None] = {}
    for variant in VARIANTS:
        for scenario in scenarios(workflow, "workflow_dispatch", {"probe": variant}):
            runs = simulate(workflow, scenario)
            reuse = scenario.outputs["uat-reuse"]["reuse"]
            green = _gates_green(runs)
            ran = runs[PROBE_PUBLISH_JOB].result != "skipped"
            if green and not ran:
                found[f"{PROBE_PUBLISH_JOB} is skipped after green gates (probe={variant}, reuse={reuse})"] = None
            if not green and ran:
                found[f"{PROBE_PUBLISH_JOB} runs although a gate did not succeed (probe={variant}, reuse={reuse})"] = (
                    None
                )
    return list(found)


def dangling_needs(workflow: dict[str, Any]) -> list[str]:
    jobs = workflow["jobs"]
    return [
        f"{name} needs {parent}, which is not a job"
        for name, job in jobs.items()
        for parent in needs_of(job)
        if parent not in jobs
    ]


def check(workflow: dict[str, Any]) -> list[str]:
    dangling = dangling_needs(workflow)
    if dangling:
        missing = [] if PROBE_PUBLISH_JOB in workflow["jobs"] else [f"the workflow has no {PROBE_PUBLISH_JOB} job"]
        return [*dangling, *missing]
    return [*real_violations(workflow), *probe_violations(workflow), *probe_publish_violations(workflow)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the release-v2 job graph under every probe and real outcome."
    )
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    args = parser.parse_args(argv)
    violations = check(load_workflow(args.workflow))
    for violation in violations:
        print(f"::error::{violation}", file=sys.stderr)
    if violations:
        return 1
    print(f"{args.workflow.name}: the job graph keeps publish gated and probe mode publish-free")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
