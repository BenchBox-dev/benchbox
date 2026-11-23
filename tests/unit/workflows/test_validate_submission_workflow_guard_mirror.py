from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "validate-submission.yml"

_STEP_NAME = "Reject validator or workflow changes in a submission PR"


def _guard_step() -> dict:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["validate"]["steps"]:
        if step.get("name") == _STEP_NAME:
            return step
    raise AssertionError(f"could not find the {_STEP_NAME!r} step in validate-submission.yml")


def test_guard_step_binds_the_same_trust_signals_as_the_vendor_gate() -> None:
    step = _guard_step()
    env = step.get("env") or {}
    assert env.get("IS_FORK") == "${{ github.event.pull_request.head.repo.fork }}"
    assert env.get("BASE_REF") == "${{ github.event.pull_request.base.ref }}"
    assert env.get("HEAD_REF") == "${{ github.head_ref }}"
    assert env.get("PR_AUTHOR") == "${{ github.event.pull_request.user.login }}"


def test_guard_step_still_covers_all_six_files() -> None:
    script = _guard_step()["run"]
    for f in [
        "scripts/validate_submission.py",
        "benchbox/validation/bundle.py",
        "benchbox/core/results/query_status.py",
        "benchbox/core/results/schema_policy.py",
        "scripts/generate_corpus_inventory.py",
        ".github/workflows/validate-submission.yml",
    ]:
        assert f in script


def _run_guard_body(*, changed_guard: str, is_fork: str, base_ref: str, head_ref: str, pr_author: str):
    script = _guard_step()["run"]
    start = script.index('if [ -n "$CHANGED_GUARD" ]; then')
    body = script[start:]
    skip_without_posix_shell()
    env = {
        "CHANGED_GUARD": changed_guard,
        "IS_FORK": is_fork,
        "BASE_REF": base_ref,
        "HEAD_REF": head_ref,
        "PR_AUTHOR": pr_author,
    }
    return run_posix_shell(
        body,
        capture_output=True,
        text=True,
        env=env,
    )


def test_trusted_mirror_pr_touching_guarded_files_passes() -> None:
    result = _run_guard_body(
        changed_guard="scripts/validate_submission.py\nbenchbox/validation/bundle.py",
        is_fork="false",
        base_ref="published-results",
        head_ref="auto/results-mirror-deadbeef",
        pr_author="github-actions[bot]",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Trusted bot-created same-repo mirror PR" in result.stdout
    assert "::error::" not in result.stdout


@pytest.mark.parametrize(
    ("is_fork", "base_ref", "head_ref", "pr_author"),
    [
        ("true", "published-results", "auto/results-mirror-deadbeef", "github-actions[bot]"),
        ("false", "develop", "auto/results-mirror-deadbeef", "github-actions[bot]"),
        ("false", "published-results", "feature/community-result", "github-actions[bot]"),
        ("false", "published-results", "auto/results-mirror-deadbeef", "maintainer"),
    ],
    ids=["fork-pr", "wrong-base", "non-matching-branch", "non-bot-author"],
)
def test_untrusted_shapes_touching_guarded_files_are_still_rejected(
    is_fork: str, base_ref: str, head_ref: str, pr_author: str
) -> None:
    result = _run_guard_body(
        changed_guard="scripts/validate_submission.py",
        is_fork=is_fork,
        base_ref=base_ref,
        head_ref=head_ref,
        pr_author=pr_author,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "::error::" in result.stdout
    assert "not allowed" in result.stdout


def test_non_mirror_pr_touching_guarded_files_is_rejected_even_without_bot_signals() -> None:
    result = _run_guard_body(
        changed_guard=".github/workflows/validate-submission.yml",
        is_fork="false",
        base_ref="published-results",
        head_ref="feature/tweak-validator",
        pr_author="some-contributor",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "::error::" in result.stdout
