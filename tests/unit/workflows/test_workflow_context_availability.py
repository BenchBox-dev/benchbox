# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

JOB_IF_ALLOWED_CONTEXTS = frozenset({"github", "needs", "vars", "inputs"})

JOB_IF_FORBIDDEN_CONTEXTS = frozenset({"matrix", "strategy", "env", "steps", "job", "runner", "secrets"})

_CONTEXT_RE = re.compile(r"(?<![\w.-])([a-z]+)\s*(?:\.|\[)", re.ASCII)


def _workflow_files() -> list[Path]:
    return sorted(p for p in WORKFLOW_DIR.glob("*.yml") if p.is_file())


def _jobs(document: object) -> dict:
    if not isinstance(document, dict):
        return {}
    jobs = document.get("jobs")
    return jobs if isinstance(jobs, dict) else {}


def _referenced_contexts(expression: object) -> set[str]:
    return set(_CONTEXT_RE.findall(str(expression)))


def test_workflow_directory_is_discoverable():
    files = _workflow_files()
    assert files, f"no workflow files found under {WORKFLOW_DIR}"


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda p: p.name)
def test_job_level_if_references_only_available_contexts(workflow):
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    offenders = []
    for job_name, config in _jobs(document).items():
        if not isinstance(config, dict) or "if" not in config:
            continue
        forbidden = _referenced_contexts(config["if"]) & JOB_IF_FORBIDDEN_CONTEXTS
        if forbidden:
            offenders.append((job_name, sorted(forbidden)))

    assert not offenders, (
        f"{workflow.name}: job-level `if` references context(s) unavailable there "
        f"({offenders}). GitHub evaluates jobs.<id>.if before the matrix expands, so only "
        f"{sorted(JOB_IF_ALLOWED_CONTEXTS)} are visible; anything else fails the WHOLE workflow "
        "at startup with zero jobs and no logs. Move the condition to the steps "
        "(step `if` can see matrix), or compute it in a job-level `env`, which can."
    )


class TestGuardDetectsTheRegression:
    def test_flags_the_original_seed_corpus_expression(self):
        expression = "github.event.inputs.benchmark == '' || github.event.inputs.benchmark == matrix.benchmark"
        assert _referenced_contexts(expression) & JOB_IF_FORBIDDEN_CONTEXTS == {"matrix"}

    @pytest.mark.parametrize(
        "expression",
        [
            "matrix.os == 'ubuntu-latest'",
            "matrix['os'] == 'ubuntu-latest'",
            "env.SELECTED == 'true'",
            "steps.filter.outputs.skip != 'true'",
            "runner.os == 'Linux'",
            "strategy.job-index == 0",
        ],
    )
    def test_flags_each_forbidden_context(self, expression):
        assert _referenced_contexts(expression) & JOB_IF_FORBIDDEN_CONTEXTS

    @pytest.mark.parametrize(
        "expression",
        [
            "always()",
            "success() && github.event_name == 'push'",
            "needs.ci-paths.outputs.needs-code-ci == 'true'",
            "inputs.benchmark == ''",
            "vars.SOME_FLAG == '1'",
            "github.ref == 'refs/heads/develop'",
            "needs.install-matrix.result == 'success'",
            "needs.uat-steps.outputs.env == 'x'",
        ],
    )
    def test_does_not_flag_legitimate_job_conditions(self, expression):
        assert not (_referenced_contexts(expression) & JOB_IF_FORBIDDEN_CONTEXTS)

    def test_step_level_matrix_reference_is_not_examined(self):
        document = yaml.safe_load(
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: only on linux\n"
            "        if: matrix.os == 'ubuntu-latest'\n"
            "        run: echo hi\n"
        )
        offenders = [
            name
            for name, config in _jobs(document).items()
            if isinstance(config, dict)
            and "if" in config
            and _referenced_contexts(config["if"]) & JOB_IF_FORBIDDEN_CONTEXTS
        ]
        assert offenders == []
