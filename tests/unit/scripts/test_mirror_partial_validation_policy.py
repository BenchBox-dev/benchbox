from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
SYNC_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sync-results-data-to-published.yml"
COMMUNITY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate-submission.yml"


def test_mirror_workflow_allows_partial_validation() -> None:
    text = SYNC_WORKFLOW.read_text(encoding="utf-8")
    assert "--allow-partial-validation" in text
    assert "partial" in text.lower()

    assert "validate_submission.py" in text
    assert "generate_corpus_inventory.py --check" in text


def _submission_validation_run() -> str:
    workflow = yaml.safe_load(COMMUNITY_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["validate"]["steps"]
    return next(step["run"] for step in steps if step.get("name") == "Validate bundles")


def _evaluate_trust_gate(*, is_fork: str, base_ref: str | None, head_ref: str, pr_author: str) -> tuple[str, str]:
    run = _submission_validation_run()
    prefix = run.split('_BASE_SHA="${BASE_SHA:-HEAD}"', maxsplit=1)[0]
    env = os.environ.copy()
    env.update({"IS_FORK": is_fork, "HEAD_REF": head_ref, "PR_AUTHOR": pr_author})
    if base_ref is not None:
        env["BASE_REF"] = base_ref
    else:
        env.pop("BASE_REF", None)
    skip_without_posix_shell()
    result = run_posix_shell(
        prefix + '\nprintf "%s|%s\\n" "$REQUIRE_MANIFEST" "$ALLOW_PARTIAL_VALIDATION"',
        capture_output=True,
        check=True,
        env=env,
        text=True,
    )
    output = result.stdout.strip()
    require_manifest, allow_partial = output.split("|", maxsplit=1)
    return require_manifest, allow_partial


def test_community_validate_submission_defaults_strict() -> None:
    run = _submission_validation_run()

    assert 'REQUIRE_MANIFEST="--require-manifest"' in run
    assert 'ALLOW_PARTIAL_VALIDATION=""' in run
    assert "$REQUIRE_MANIFEST $ALLOW_PARTIAL_VALIDATION" in run


def test_partial_waiver_binds_to_published_results_base() -> None:
    workflow = yaml.safe_load(COMMUNITY_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["validate"]["steps"]
    validate_step = next(step for step in steps if step.get("name") == "Validate bundles")
    assert validate_step["env"]["BASE_REF"] == "${{ github.event.pull_request.base.ref }}"
    assert '"${BASE_REF:-}" = "published-results"' in validate_step["run"]


@pytest.mark.parametrize(
    ("is_fork", "base_ref", "head_ref", "pr_author"),
    [
        ("true", "published-results", "auto/results-mirror-deadbeef", "github-actions[bot]"),
        ("false", "develop", "auto/results-mirror-deadbeef", "github-actions[bot]"),
        ("false", "published-results", "feature/community-result", "github-actions[bot]"),
        ("false", "published-results", "auto/results-mirror-deadbeef", "maintainer"),
    ],
)
def test_partial_waiver_rejects_untrusted_mirror_shapes(
    is_fork: str, base_ref: str, head_ref: str, pr_author: str
) -> None:
    assert _evaluate_trust_gate(is_fork=is_fork, base_ref=base_ref, head_ref=head_ref, pr_author=pr_author) == (
        "--require-manifest",
        "",
    )


def test_partial_waiver_accepts_exact_bot_created_same_repo_mirror() -> None:
    assert _evaluate_trust_gate(
        is_fork="false",
        base_ref="published-results",
        head_ref="auto/results-mirror-deadbeef",
        pr_author="github-actions[bot]",
    ) == ("", "--allow-partial-validation")


def test_partial_waiver_missing_base_fails_closed() -> None:
    assert _evaluate_trust_gate(
        is_fork="false",
        base_ref=None,
        head_ref="auto/results-mirror-deadbeef",
        pr_author="github-actions[bot]",
    ) == ("--require-manifest", "")


def test_community_message_string_still_requires_passed() -> None:
    bundle = (REPO_ROOT / "benchbox" / "validation" / "bundle.py").read_text(encoding="utf-8")
    assert "must be 'passed' for public submissions" in bundle


