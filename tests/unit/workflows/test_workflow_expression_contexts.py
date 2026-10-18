from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _workflows() -> list[Path]:
    root = Path(__file__).resolve().parents[3] / ".github" / "workflows"
    return sorted([*root.glob("*.yml"), *root.glob("*.yaml")])


WORKFLOWS = _workflows()

KNOWN_CONTEXTS = {
    "github",
    "env",
    "vars",
    "job",
    "jobs",
    "steps",
    "runner",
    "secrets",
    "strategy",
    "matrix",
    "needs",
    "inputs",
}
NON_CONTEXT_WORDS = {
    "always",
    "success",
    "failure",
    "cancelled",
    "contains",
    "startsWith",
    "endsWith",
    "format",
    "join",
    "toJSON",
    "toJson",
    "fromJSON",
    "hashFiles",
    "true",
    "false",
    "null",
}
JOB_ENV_FORBIDDEN = {"runner", "env", "steps", "job"}

EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
IDENTIFIER = re.compile(r"(?<![\w.'\"-])([A-Za-z_][A-Za-z0-9_-]*)")
STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


def _identifiers(expression: str) -> set[str]:
    return set(IDENTIFIER.findall(STRING_LITERAL.sub("''", expression)))


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_expressions_name_known_contexts(workflow: Path) -> None:
    text = workflow.read_text(encoding="utf-8")
    unknown = sorted(
        {
            name
            for expression in EXPRESSION.findall(text)
            for name in _identifiers(expression)
            if name not in KNOWN_CONTEXTS and name not in NON_CONTEXT_WORDS
        }
    )
    assert not unknown, f"{workflow.name}: expression names unknown context(s) {unknown}"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_job_level_env_uses_only_available_contexts(workflow: Path) -> None:
    jobs: dict[str, Any] = yaml.safe_load(workflow.read_text(encoding="utf-8")).get("jobs") or {}
    offenders = []
    for job_id, job in jobs.items():
        for key, value in ((job or {}).get("env") or {}).items():
            for expression in EXPRESSION.findall(str(value)):
                bad = _identifiers(expression) & JOB_ENV_FORBIDDEN
                if bad:
                    offenders.append(f"jobs.{job_id}.env.{key} uses {sorted(bad)}")
    assert not offenders, f"{workflow.name}: " + "; ".join(offenders)


def test_detector_flags_the_known_failure_shapes() -> None:
    assert _identifiers("number") == {"number"}
    assert "runner" in _identifiers(" runner.temp ")
    assert _identifiers("github.event_name == 'merge_group' && '600' || '0'") == {"github"}
    assert _identifiers("matrix.python-version") == {"matrix"}
