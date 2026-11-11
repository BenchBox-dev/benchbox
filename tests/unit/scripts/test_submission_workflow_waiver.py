from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "validate-submission.yml"


def _validate_step() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["validate"]["steps"]
    for step in steps:
        if step.get("id") == "validate":
            return step
    raise AssertionError("validate-submission.yml has no step with id: validate")


def test_waiver_env_binds_unforgeable_fork_signal() -> None:
    env = _validate_step().get("env") or {}
    fork_bindings = {k: v for k, v in env.items() if "head.repo.fork" in str(v)}
    assert fork_bindings, (
        "The 'Validate bundles' step must bind github.event.pull_request."
        "head.repo.fork into the env so the manifest waiver can gate on it. "
        "github.head_ref alone is attacker-controlled on fork PRs and spoofable."
    )


def test_waiver_gates_manifest_skip_on_fork_check() -> None:
    step = _validate_step()
    env = step.get("env") or {}
    run = step["run"]

    fork_var = next(
        (k for k, v in env.items() if "head.repo.fork" in str(v)),
        None,
    )
    assert fork_var is not None, "no env var bound to head.repo.fork"
    author_var = next(
        (k for k, v in env.items() if "pull_request.user.login" in str(v)),
        None,
    )
    assert author_var is not None, "no env var bound to pull_request.user.login"

    assert re.search(rf'\[\s*"\${fork_var}"\s*=\s*"false"\s*\]', run), (
        "The manifest waiver must be nested under an "
        f'[ "${fork_var}" = "false" ] guard; a fork PR must never reach the '
        "REQUIRE_MANIFEST='' branch."
    )
    assert "auto/results-mirror-*" in run, "The waiver should still narrow to the mirror branch pattern."

    guard_match = re.search(
        rf'\[\s*"\${fork_var}"\s*=\s*"false"\s*\]\s*&&\s*'
        rf'\[\s*"\${author_var}"\s*=\s*"github-actions\[bot\]"\s*\]\s*;\s*then\n(.*?)\nfi\b',
        run,
        re.DOTALL,
    )
    assert guard_match is not None, (
        "Could not find a trust guard requiring both a same-repository head and "
        "the exact github-actions[bot] PR author."
    )
    guarded_body = guard_match.group(1)
    assert "auto/results-mirror-*" in guarded_body, (
        "The mirror branch pattern check must be NESTED inside the "
        f'[ "${fork_var}" = "false" ] guard body, not merely present elsewhere in the step - '
        "otherwise a same-named branch on a fork PR could still reach the waiver."
    )
    assert re.search(r'REQUIRE_MANIFEST\s*=\s*""', guarded_body), (
        'The REQUIRE_MANIFEST="" assignment (the actual waiver) must be NESTED inside the '
        f'[ "${fork_var}" = "false" ] guard body, not merely present elsewhere in the step - '
        "otherwise a fork PR could reach the waiver regardless of the fork check's outcome."
    )


def test_default_requires_manifest() -> None:
    run = _validate_step()["run"]
    assert 'REQUIRE_MANIFEST="--require-manifest"' in run, (
        "REQUIRE_MANIFEST must default to --require-manifest (fail closed); "
        "the waiver only clears it inside the fork+branch guard."
    )


