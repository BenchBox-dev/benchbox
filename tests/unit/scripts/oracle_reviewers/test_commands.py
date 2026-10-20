from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers.commands import build
from _project.scripts.oracle_reviewers.policy import Policy

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WS = Path("/work/space")
PROMPT = "review this"


def test_build_dispatches_omp_reviewer_with_selector_and_empty_effort(policy: Policy, tmp_path: Path) -> None:
    reviewer = policy.reviewers["small"]
    invocation = build(reviewer, WS, PROMPT, tmp_path)
    assert invocation.argv[invocation.argv.index("--model") + 1] == reviewer.model
    assert "--thinking" not in invocation.argv
    assert invocation.argv[-1] == PROMPT


def test_build_passes_explicit_effort_through(policy: Policy, tmp_path: Path) -> None:
    reviewer = replace(policy.reviewers["small"], effort="low")
    invocation = build(reviewer, WS, PROMPT, tmp_path)
    assert invocation.argv[invocation.argv.index("--thinking") + 1] == "low"
    assert invocation.argv[-1] == PROMPT


def test_build_rejects_invalid_effort(policy: Policy, tmp_path: Path) -> None:
    reviewer = replace(policy.reviewers["small"], effort="turbo")
    with pytest.raises(ValueError, match="thinking level"):
        build(reviewer, WS, PROMPT, tmp_path)


def test_build_rejects_unknown_harness(policy: Policy, tmp_path: Path) -> None:
    reviewer = replace(policy.reviewers["small"], harness="codex")
    with pytest.raises(ValueError, match="unknown harness"):
        build(reviewer, WS, PROMPT, tmp_path)
