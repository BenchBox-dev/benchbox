from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "validate-submission.yml"

_VALIDATOR_CALL = (
    "xargs -d '\\n' uv run -- python scripts/validate_submission.py "
    "$REQUIRE_MANIFEST $ALLOW_PARTIAL_VALIDATION --pr-comment /tmp/pr_comment.md < /tmp/changed_bundles.txt"
)
_VALIDATOR_CALL_WITH_CORPUS = (
    "xargs -d '\\n' uv run -- python scripts/validate_submission.py "
    "$REQUIRE_MANIFEST $ALLOW_PARTIAL_VALIDATION $CORPUS_ARG --pr-comment /tmp/pr_comment.md < /tmp/changed_bundles.txt"
)


def _validate_step() -> dict:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return next(step for step in workflow["jobs"]["validate"]["steps"] if step.get("id") == "validate")


def _run_step_with_validator_exiting(status: int, tmp_path: Path):
    script = _validate_step()["run"]
    matched = _VALIDATOR_CALL_WITH_CORPUS if _VALIDATOR_CALL_WITH_CORPUS in script else _VALIDATOR_CALL
    assert matched in script, (
        "the validator invocation in validate-submission.yml no longer matches what this test "
        "substitutes; update _VALIDATOR_CALL so the rung keeps exercising the real block"
    )
    script = script.replace(matched, f"(exit {status})")
    _PARITY_CALL = (
        'uv run -- python scripts/publication/validator_parity.py --base-sha "$_BASE_SHA" '
        '--merge-sha "$_MERGE_SHA" --head-sha "$_HEAD_SHA" --corpus-changed-paths "$_CORPUS_FILE" '
        "$REQUIRE_MANIFEST $ALLOW_PARTIAL_VALIDATION"
    )
    assert _PARITY_CALL in script, (
        "the validator_parity invocation in validate-submission.yml no longer matches what this "
        "test substitutes; update _PARITY_CALL so the rung keeps exercising the real block"
    )
    script = script.replace(_PARITY_CALL, "(exit 0)")
    return run_posix_shell(
        f"set -e\n{script}",
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            "IS_FORK": "false",
            "HEAD_REF": "auto/results-mirror-deadbeef",
            "PR_AUTHOR": "github-actions[bot]",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )


def test_failing_validator_fails_the_step(tmp_path: Path) -> None:
    skip_without_posix_shell()

    result = _run_step_with_validator_exiting(1, tmp_path)

    assert result.returncode != 0, (
        "the Validate bundles step exited 0 with a FAILING validator -- the submission gate is "
        "fail-open. The validator's status is being swallowed by the `| tee` pipeline (GitHub's "
        "default shell is `bash -e`, without `-o pipefail`), so bad bundles reach "
        "published-results with a green check. See PR #1629."
    )


def test_passing_validator_still_passes_the_step(tmp_path: Path) -> None:
    skip_without_posix_shell()

    result = _run_step_with_validator_exiting(0, tmp_path)

    assert result.returncode == 0, (
        f"the Validate bundles step exited {result.returncode} with a PASSING validator; stderr:\n{result.stderr}"
    )


def test_corpus_inventory_step_is_gated_on_the_validate_outcome() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    step = next(s for s in workflow["jobs"]["validate"]["steps"] if s.get("name") == "Check corpus inventory")

    assert step["if"] in (
        "steps.validate.outcome == 'success'",
        "steps.validate.outcome == 'success' || steps.changed.outputs.has_bundles == 'corpus-only'",
    )


