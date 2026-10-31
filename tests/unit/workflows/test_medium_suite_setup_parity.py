from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS_DIR = Path(__file__).resolve().parents[3] / ".github" / "workflows"

MEDIUM_JOBS = {
    "ci.yml:medium-test": ("ci.yml", "medium-test"),
    "trunk.yml:medium-test": ("trunk.yml", "medium-test"),
    "trunk-coverage.yml:medium-test": ("trunk-coverage.yml", "medium-test"),
    "nightly-v2.yml:integration": ("nightly-v2.yml", "integration"),
    "test.yml:medium": ("test.yml", "medium"),
}

ALLOWED_MISSING: dict[str, dict[str, str]] = {}

MATRIX_CONDITION = re.compile(r"^matrix\.(\w+) == '([^']*)'$")


def _job(workflow: str, job_id: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS_DIR / workflow).read_text(encoding="utf-8"))["jobs"][job_id]


def _signature(step: dict[str, Any]) -> str:
    return step.get("uses") or step["run"].strip()


def _starts_test_phase(step: dict[str, Any]) -> bool:
    if str(step.get("uses", "")).startswith("actions/download-artifact@"):
        return True
    run = step.get("run", "")
    return "pytest" in run or "release_canary_sharding" in run


def _matrix_entries(job: dict[str, Any]) -> list[dict[str, Any]]:
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    return matrix.get("include") or [{}]


def _runs_for_some_entry(condition: Any, entries: list[dict[str, Any]]) -> bool:
    if condition is None:
        return True
    match = MATRIX_CONDITION.match(str(condition).strip())
    assert match, f"unsupported provisioning condition: {condition!r}"
    key, value = match.groups()
    return any(str(entry.get(key)) == value for entry in entries)


def _provisioning(job: dict[str, Any]) -> dict[str, str]:
    entries = _matrix_entries(job)
    found: dict[str, str] = {}
    for step in job["steps"]:
        if _starts_test_phase(step):
            break
        if _runs_for_some_entry(step.get("if"), entries):
            found[_signature(step)] = step.get("name", "")
    return found


def _all_provisioning() -> dict[str, dict[str, str]]:
    return {label: _provisioning(_job(*location)) for label, location in MEDIUM_JOBS.items()}


def test_every_medium_job_has_provisioning_steps() -> None:
    for label, steps in _all_provisioning().items():
        assert steps, label


def test_medium_jobs_share_provisioning_steps() -> None:
    provisioning = _all_provisioning()
    union: dict[str, str] = {}
    for steps in provisioning.values():
        union.update(steps)
    problems = []
    for label, steps in provisioning.items():
        allowed = ALLOWED_MISSING.get(label, {})
        for signature, name in union.items():
            if signature not in steps and signature not in allowed:
                problems.append(f"{label} is missing provisioning step {name!r}: {signature}")
    assert not problems, "\n".join(problems)


def test_provisioning_allowlist_has_no_stale_entries() -> None:
    provisioning = _all_provisioning()
    union = {signature for steps in provisioning.values() for signature in steps}
    stale = []
    for label, allowed in ALLOWED_MISSING.items():
        assert label in provisioning, f"unknown job in allowlist: {label}"
        for signature in allowed:
            if signature in provisioning[label]:
                stale.append(f"{label} already has allowlisted step: {signature}")
            elif signature not in union:
                stale.append(f"{label} allowlists a step no job has: {signature}")
    assert not stale, "\n".join(stale)


def test_nightly_core_only_sync_counts_as_provisioned() -> None:
    steps = _provisioning(_job("nightly-v2.yml", "integration"))
    assert "uv sync --project _project/scripts --locked" in steps
